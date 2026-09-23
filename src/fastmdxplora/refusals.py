"""What a refusal is, in a word a program can read.

This software refuses a great deal, on purpose, and says why in English.
The English is the point: a person who is told that a ligand's protonation
at pH 7.4 is not determined has been told something they can act on, and
no code could replace that sentence.

But the sentence is all there is. Three hundred and fifty-eight places
raise, and a caller that wants to know *which* refusal it received has to
read prose and guess. Three consequences, all of them already visible in
this repository:

  1. :mod:`fastmdxplora.validation.corpus` matches a substring
     (``mentioning``) so a case cannot pass by refusing for an unrelated
     reason. That is the right instinct implemented with the only tool
     available. Reword a message and the measurement silently changes
     what it measures.
  2. A caller driving the package from a script cannot distinguish "the
     structure is ambiguous, ask a human" from "RDKit is not installed,
     install it" without parsing text.
  3. Nothing can count refusals by kind across a corpus, so the rate this
     software most wants to report -- how often it refuses, and for what
     -- has to be assembled by hand.

So every refusal gains a stable identifier alongside its prose. The prose
does not change. Nothing that reads ``str(exc)`` today reads anything
different tomorrow.

Two properties make the identifier worth having rather than merely
present.

**It is hierarchical.** ``setup.chemistry.protonation_undetermined`` can be
matched exactly, or by the family ``setup.chemistry.*``, or by the phase
``setup.*``. A caller that wants to handle every chemistry refusal the
same way says so once.

**It carries what may be disclosed.** This is the load-bearing field, and
the reason this module is a registry rather than a list of strings. A
refusal because ``box_shape`` was misspelled can safely be answered with
the list of shapes: the schema knows the complete set, and there is no
judgement in it. A refusal because a ligand's pKa sits inside the pH
margin cannot be answered at all -- the software does not know the answer,
and a suggestion would be a guess wearing the package's authority. The
registry records which of these each code is, so an automated caller can
be told everything the software actually knows and nothing it does not.

    The validator may say what the schema permits. It may never say what
    the chemistry requires.

Stability
---------
The identifiers are a public interface, versioned with the package and
subject to :data:`SUPERSEDED` rather than to silent renaming. Adding a
code is a minor change. Changing what an existing code means is not, and
the :data:`SUPERSEDED` table exists so it never has to be done by
surprise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

__all__ = [
    "Code",
    "CODES",
    "CodedError",
    "StudyError",
    "CodedKeyError",
    "MissingResultError",
    "MissingPathError",
    "OutputExistsError",
    "BackendUnavailable",
    "BackendDefect",
    "UnstableRun",
    "Kind",
    "Disclosure",
    "Refusal",
    "code_for",
    "codes_under",
    "known",
    "refusal_of",
    "resolve",
    "SUPERSEDED",
]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
class Kind:
    """What sort of thing went wrong, which decides who can fix it.

    The four are distinguished because the right response differs. A
    ``STRUCTURAL`` refusal is answerable from the schema and a caller can
    retry immediately with a corrected value. A ``SEMANTIC`` one needs a
    decision about the system that the software does not hold. An
    ``ENVIRONMENTAL`` one is about the machine rather than the study, and
    the same config will run elsewhere. ``INSUFFICIENT`` means the study
    is well-formed and there is not enough data to answer it, which is
    the only one of the four that more computing can fix.
    """

    #: The config says something the schema does not permit, or omits
    #: something it requires. Answerable from the declaration alone.
    STRUCTURAL = "structural"

    #: The study is well-formed and the system does not determine what to
    #: simulate. Needs a decision, not a correction.
    SEMANTIC = "semantic"

    #: The machine is missing a backend, or an external service did not
    #: answer. Nothing about the study is wrong.
    ENVIRONMENTAL = "environmental"

    #: There is not enough data to support the answer that was asked for.
    #: More sampling, more frames, more windows.
    INSUFFICIENT = "insufficient"

    ALL = (STRUCTURAL, SEMANTIC, ENVIRONMENTAL, INSUFFICIENT)


class Disclosure:
    """How much an automated caller may be told about the remedy.

    The distinction this software turns on. Telling a caller the set of
    legal box shapes costs nothing and saves a round trip, because the
    schema holds that set and it is complete. Telling a caller what
    protonation state to use would be inventing an answer.

    A middle case earns its own value: naming the setting that is missing
    or in conflict is a fact about the config, not about the chemistry,
    and withholding it would make the caller search for something the
    software already knows.
    """

    #: The complete legal set is known and may be given.
    PERMITTED_VALUES = "permitted_values"

    #: The offending or missing setting may be named; its value may not
    #: be suggested.
    FIELD_ONLY = "field_only"

    #: An exact remedy exists and is not a scientific judgement -- an
    #: install command, a path to correct.
    ACTION = "action"

    #: The software does not know the answer. Nothing may be suggested.
    NOTHING = "nothing"

    ALL = (PERMITTED_VALUES, FIELD_ONLY, ACTION, NOTHING)


# ---------------------------------------------------------------------------
# Code descriptor
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Code:
    """One refusal, named.

    Parameters
    ----------
    id : str
        Dotted, lowercase, hierarchical. ``<phase>.<family>.<what>``. The
        stable public identifier; this is what appears in a manifest and
        what a caller matches on.
    summary : str
        One line, in the present tense, describing the condition rather
        than the remedy. Not the user-facing message -- that stays at the
        raise site, where it has the particulars.
    kind : str
        One of :class:`Kind`.
    disclosure : str
        One of :class:`Disclosure`.
    retryable : bool
        Whether the identical study may succeed on a later attempt without
        anything changing. True only for transient external failures. A
        missing backend is not retryable: something has to be installed
        first, which is a change.
    since : str
        The package version the code was introduced in.
    detail_keys : tuple of str
        Keys this code's ``details`` mapping is expected to carry, where
        there are any. Declared so a caller knows what to read without
        catching one first, and so the tests can hold raise sites to it.
        ``permitted`` is the conventional key for the legal set.
    """

    id: str
    summary: str
    kind: str
    disclosure: str
    retryable: bool = False
    since: str = "2.4"
    detail_keys: tuple[str, ...] = ()

    @property
    def phase(self) -> str:
        """The leading segment: which phase or concern this belongs to."""
        return self.id.split(".", 1)[0]

    @property
    def family(self) -> str:
        """Everything but the last segment."""
        return self.id.rsplit(".", 1)[0]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.id


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------
# Ordered by phase, then family, then alphabetically within it. A new code
# goes next to its siblings, not at the end.
CODES: tuple[Code, ...] = (
    # -- config -------------------------------------------------------------
    Code("config.file.missing",
         "The named config file does not exist.",
         Kind.STRUCTURAL, Disclosure.ACTION,
         detail_keys=("path",)),
    Code("config.file.unparseable",
         "The file is not valid YAML.",
         Kind.STRUCTURAL, Disclosure.ACTION,
         detail_keys=("path",)),
    Code("config.file.not_a_mapping",
         "The file parsed, and its top level is not a mapping.",
         Kind.STRUCTURAL, Disclosure.ACTION,
         detail_keys=("path", "found_type")),
    Code("config.option.unknown",
         "A key the schema does not declare.",
         Kind.STRUCTURAL, Disclosure.PERMITTED_VALUES,
         detail_keys=("option", "context", "permitted", "suggestion")),
    # FIELD_ONLY rather than PERMITTED_VALUES: a type is not a set of
    # values. `expected_type` is disclosed and is a fact about the schema,
    # but a caller cannot iterate it, and a code that promises `permitted`
    # should deliver something iterable.
    Code("config.option.wrong_type",
         "A declared key carrying a value of the wrong type.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("option", "context", "expected_type", "found_type")),
    Code("config.option.out_of_range",
         "A numeric setting outside what the quantity can be.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("option", "given", "minimum", "maximum")),
    Code("config.option.not_permitted",
         "A declared key carrying a value outside its declared choices.",
         Kind.STRUCTURAL, Disclosure.PERMITTED_VALUES,
         detail_keys=("option", "context", "given", "permitted", "suggestion")),
    Code("config.option.conflicting",
         "Two settings given that cannot both apply.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("options",)),
    Code("config.option.missing_companion",
         "A setting that requires another, given without it.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("option", "requires")),
    Code("config.option.inapplicable",
         "A setting that has no meaning in the context it was given in.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("option", "context")),
    Code("config.phase.unknown",
         "An include/exclude list naming a phase that does not exist.",
         Kind.STRUCTURAL, Disclosure.PERMITTED_VALUES,
         detail_keys=("given", "permitted")),
    Code("config.untranslatable",
         "A decided setting the target language cannot express.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("option", "language")),

    # -- environment --------------------------------------------------------
    Code("environment.backend.missing",
         "An optional chemistry backend the requested phase needs.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION,
         detail_keys=("packages", "install_command")),
    Code("environment.backend.defective",
         "An optional backend is installed and returning wrong answers, so "
         "the measurement cannot be made on this platform.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION,
         detail_keys=("backend", "attempts")),
    Code("environment.service.unreachable",
         "An external service did not answer.",
         Kind.ENVIRONMENTAL, Disclosure.NOTHING, retryable=True,
         detail_keys=("url",)),
    Code("environment.service.unusable_response",
         "An external service answered with something unreadable.",
         Kind.ENVIRONMENTAL, Disclosure.NOTHING, retryable=True,
         detail_keys=("url", "resource")),
    Code("environment.service.machine_unreachable",
         "A machine did not answer over SSH.",
         Kind.ENVIRONMENTAL, Disclosure.NOTHING, retryable=True,
         detail_keys=("machine", "reason")),
    Code("environment.service.machine_unreadable",
         "A machine answered an inspection with something that could not "
         "be read.",
         Kind.ENVIRONMENTAL, Disclosure.NOTHING, retryable=True,
         detail_keys=("machine",)),
    Code("environment.path.not_found",
         "A file the study names is not on disk.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION,
         detail_keys=("path",)),
    Code("environment.path.exists",
         "Writing here would overwrite something.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION,
         detail_keys=("path",)),
    Code("environment.budget.absent",
         "An unattended run was asked for with no ceiling on what it may "
         "spend.",
         Kind.INSUFFICIENT, Disclosure.FIELD_ONLY,
         detail_keys=("mode",)),
    Code("environment.budget.exhausted",
         "Starting this job would take the campaign past the allowance it "
         "was given.",
         Kind.ENVIRONMENTAL, Disclosure.FIELD_ONLY,
         detail_keys=("estimate_hours", "remaining_hours")),
    Code("simulation.run.abandoned",
         "A segment that never ran, because an earlier one settled the "
         "question it was part of.",
         Kind.SEMANTIC, Disclosure.NOTHING),
    Code("environment.model.unset",
         "No model has been chosen, so there is nothing to ask.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION),
    Code("environment.credentials.absent",
         "No API key for the chosen provider, in the environment or stored.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION,
         detail_keys=("provider", "environment_variable")),
    Code("environment.calibration.absent",
         "This machine has not been measured, so there is no basis for a "
         "duration estimate.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION),
    Code("environment.calibration.inconsistent",
         "Runs on this machine disagree about what a particle-step costs by "
         "more than the cost model's assumption allows.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION,
         detail_keys=("spread", "runs")),
    Code("environment.calibration.stale",
         "The stored measurement was taken on different hardware or under "
         "different settings than the study asks for.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION,
         detail_keys=("measured_on", "asked_about")),
    Code("environment.platform.unavailable",
         "The requested compute platform did not load or did not run.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION,
         detail_keys=("requested", "available")),

    # -- setup: structure ---------------------------------------------------
    Code("setup.input.unrecognised",
         "A system input that is neither a path, a PDB identifier, nor a "
         "sequence.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("given",)),
    Code("setup.structure.implausible_extent",
         "The prepared structure spans further than its residue count can "
         "account for, which usually means periodic images were not rejoined.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("extent_nm", "residues")),
    Code("setup.structure.undetermined",
         "The deposited entry does not determine what should be simulated.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("components",)),
    Code("setup.prepared.mismatch",
         "A shared prepared system was built with other setup settings than "
         "the study now asks for.",
         Kind.SEMANTIC, Disclosure.ACTION,
         detail_keys=("changed",)),
    Code("setup.structure.assembly_ambiguous",
         "The structure's biological assemblies hold different things, so "
         "which is simulated has to be named.",
         Kind.SEMANTIC, Disclosure.ACTION,
         detail_keys=("assemblies",)),
    Code("setup.structure.chain_unknown",
         "A chain was named that the structure does not hold.",
         Kind.STRUCTURAL, Disclosure.PERMITTED_VALUES,
         detail_keys=("given", "permitted")),
    # Two grammars, not a list of legal strings. `accepted_forms` names
    # them; there is nothing to enumerate.
    Code("setup.structure.mutation_unparseable",
         "A mutation was written in neither accepted form.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("given", "accepted_forms")),
    Code("setup.structure.mutation_mismatch",
         "The original residue named by a mutation is not at that position.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("mutation", "found", "position")),

    # -- setup: chemistry ---------------------------------------------------
    Code("setup.chemistry.unavailable",
         "The chemistry needed to parameterize a component was not obtained.",
         Kind.ENVIRONMENTAL, Disclosure.NOTHING,
         detail_keys=("resname",)),
    Code("setup.chemistry.uninterpretable",
         "Chemistry was obtained and could not be read.",
         Kind.ENVIRONMENTAL, Disclosure.NOTHING,
         detail_keys=("resname",)),
    Code("setup.chemistry.atom_count_mismatch",
         "The retrieved chemistry has a different heavy-atom count than "
         "the structure.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("resname", "expected", "found")),
    Code("setup.chemistry.protonation_undetermined",
         "The component's protonation at the study's pH is not determined.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("resname", "ph", "groups", "margin")),
    Code("setup.chemistry.charge_undetermined",
         "The component's net charge was not determined.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("resname",)),
    Code("setup.chemistry.charge_contradicted",
         "The stated net charge disagrees with the supplied file.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("resname", "stated", "found", "path")),

    # -- setup: ligand ------------------------------------------------------
    Code("setup.ligand.format_unsupported",
         "The ligand file's extension is not one that can be read.",
         Kind.STRUCTURAL, Disclosure.PERMITTED_VALUES,
         detail_keys=("path", "given", "permitted")),
    Code("setup.ligand.unreadable",
         "The ligand file could not be parsed as a molecule.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("path",)),
    Code("setup.ligand.multiple_molecules",
         "The ligand file holds more than one molecule.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("path", "count")),
    Code("setup.ligand.pose_unavailable",
         "The pose policy asked for a source that does not hold one.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("policy", "reason")),
    Code("setup.ligand.clash",
         "The ligand pose overlaps the protein beyond the threshold.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("worst_distance_nm", "threshold_nm", "pairs")),

    # -- setup: force field and membrane ------------------------------------
    Code("setup.forcefield.unknown",
         "A force field name outside the registry.",
         Kind.STRUCTURAL, Disclosure.PERMITTED_VALUES,
         detail_keys=("given", "permitted")),
    Code("setup.forcefield.incompatible",
         "The chosen force field cannot describe what the study contains.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("forcefield", "needs")),
    Code("setup.membrane.orientation_unchecked",
         "A bilayer was asked for and the protein's orientation relative to "
         "the membrane normal has not been established.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("lipid",)),
    Code("setup.membrane.lipid_unparameterized",
         "The force field files given do not describe the chosen lipid.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("lipid",)),

    # -- simulation: collective variables -----------------------------------
    Code("simulation.cone.unmeasured",
         "A window's angular wall was never measured, so there is nothing "
         "to restrain the angle against.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("window",)),
    Code("simulation.cone.too_narrow",
         "The path fits only in a cone that leaves the ligand too little "
         "room to be a bulk state.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("half_angle_deg",)),
    Code("simulation.cone.windows_outside",
         "Windows that would start outside the cone they are to run under.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("outside", "windows", "half_angle_deg")),
    Code("simulation.cv.unknown",
         "A collective variable outside the registry.",
         Kind.STRUCTURAL, Disclosure.PERMITTED_VALUES,
         detail_keys=("given", "permitted")),
    Code("simulation.cv.missing_companion",
         "A collective variable given without a setting it requires.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("variable", "requires")),
    Code("simulation.cv.inapplicable_setting",
         "A setting given to a collective variable that has no use for it.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("variable", "option")),
    Code("simulation.cv.selection_empty",
         "A selection inside a collective variable matched no atoms.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("expression", "role")),
    Code("simulation.cv.selection_arity",
         "A selection matched a number of atoms the variable cannot use.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("expression", "expected", "found", "role")),
    Code("simulation.cv.unbounded",
         "A variable that would leave the ligand in bulk, given without a "
         "wall or a funnel to bound it.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("variable",)),

    # -- simulation: biasing and results ------------------------------------
    Code("simulation.bias.parameter_missing",
         "A biasing method given without a parameter it requires.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("method", "requires")),
    Code("simulation.bias.dimension_mismatch",
         "The dimensionality of a biased run and what was asked of it "
         "disagree.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("expected", "found", "source")),
    Code("simulation.bias.no_deposit",
         "A biased run deposited nothing, so it did not bias anything.",
         Kind.INSUFFICIENT, Disclosure.NOTHING,
         detail_keys=("path",)),
    Code("simulation.restraint.selection_arity",
         "A restraint selection matched a number of atoms the restraint "
         "cannot use.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("expression", "expected", "found")),
    Code("simulation.windows.too_few",
         "Fewer windows than the method needs.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("found", "needed")),
    Code("simulation.windows.no_sampling",
         "Windows that produced no usable sampling, so there is no free "
         "energy to report.",
         Kind.INSUFFICIENT, Disclosure.FIELD_ONLY,
         detail_keys=("windows",)),
    Code("simulation.seed.unusable",
         "A seeded window's starting state is not one a run can begin from.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("window", "potential", "measured")),
    Code("simulation.reference.unusable",
         "A reference structure was given and does not support the "
         "measurement.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("path", "reason")),
    Code("simulation.resume.checkpoint_truncated",
         "A checkpoint that is not the whole file that was written.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("path", "found", "expected")),
    Code("simulation.resume.unsealed",
         "A segment that did not finish: its checkpoints were written "
         "along the way and none at the end.",
         Kind.SEMANTIC, Disclosure.ACTION,
         detail_keys=("path",)),
    Code("simulation.resume.checkpoint_rejected",
         "A checkpoint that does not belong to this system, platform or "
         "precision.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("path",)),
    Code("simulation.resume.bias_not_carried",
         "The run deposits bias that a checkpoint does not restore, so it "
         "cannot be split into segments.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("method", "segments")),
    Code("simulation.resume.time_dependent_bias",
         "The run's restraint is placed by step number, so a resumed piece "
         "would pull from an anchor the system is not at.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("method", "segments")),
    Code("simulation.resume.would_reequilibrate",
         "A checkpoint from production was to be minimised or equilibrated "
         "again, which throws away its velocities and makes the continuation "
         "a new run from a snapshot rather than the same trajectory.",
         Kind.SEMANTIC, Disclosure.ACTION,
         detail_keys=("path", "stage", "step", "minimize", "nvt_steps", "npt_steps")),
    Code("simulation.resume.interval_differs",
         "Segments written at different frame spacings, so a joined "
         "trajectory would change the time a frame represents partway.",
         Kind.SEMANTIC, Disclosure.ACTION,
         detail_keys=("path", "intervals")),
    Code("simulation.resume.timestep_differs",
         "A checkpoint was to be continued with a different timestep from "
         "the one that wrote it; the integrator state it carries is for the "
         "other.",
         Kind.SEMANTIC, Disclosure.ACTION,
         detail_keys=("path", "written_fs", "requested_fs")),
    Code("simulation.run.unstable",
         "The integration produced a non-finite state.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("step", "diagnosis")),

    # -- analysis -----------------------------------------------------------
    Code("analysis.unknown",
         "An analysis outside the registry.",
         Kind.STRUCTURAL, Disclosure.PERMITTED_VALUES,
         detail_keys=("given", "permitted", "suggestion")),
    Code("analysis.option.not_permitted",
         "An analysis option outside its declared choices.",
         Kind.STRUCTURAL, Disclosure.PERMITTED_VALUES,
         detail_keys=("analysis", "option", "given", "permitted")),
    Code("analysis.option.wrong_type",
         "An analysis option carrying a value of the wrong type.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("analysis", "option", "expected_type", "found_type")),
    Code("analysis.option.inapplicable",
         "An option given to an analysis that has no use for it.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("analysis", "option", "applies_to")),
    Code("analysis.option.missing_companion",
         "An analysis given without a setting it requires.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("analysis", "requires")),
    Code("analysis.selection.empty",
         "A selection matched no atoms in this trajectory.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("expression", "role")),
    Code("analysis.option.out_of_range",
         "A frame index or slice past the end of the trajectory.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("option", "given", "frames")),
    Code("analysis.selection.arity",
         "A selection matched a number of atoms the analysis cannot use.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("expression", "expected", "found")),
    Code("analysis.trajectory.unreadable",
         "The trajectory or topology could not be loaded.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION,
         detail_keys=("path", "reason")),
    Code("analysis.system.inapplicable",
         "The analysis does not apply to this system.",
         Kind.SEMANTIC, Disclosure.NOTHING,
         detail_keys=("analysis", "reason")),
    Code("analysis.data.absent",
         "What the analysis reads was not produced by this study.",
         Kind.SEMANTIC, Disclosure.FIELD_ONLY,
         detail_keys=("analysis", "needs")),

    # -- analysis: not enough to answer with --------------------------------
    # The family this software exists to take seriously. Separated from
    # `semantic` because more computing is the remedy, and a caller that
    # can extend a run should be able to recognise that without reading
    # prose.
    Code("analysis.sampling.too_few_frames",
         "Fewer frames than the computation requires.",
         Kind.INSUFFICIENT, Disclosure.FIELD_ONLY,
         detail_keys=("needed", "found", "analysis")),
    Code("analysis.sampling.too_few_residues",
         "Fewer residues matched than the computation requires.",
         Kind.INSUFFICIENT, Disclosure.FIELD_ONLY,
         detail_keys=("needed", "found")),
    Code("analysis.sampling.no_variance",
         "The quantity does not vary over the trajectory, so there is "
         "nothing to decompose.",
         Kind.INSUFFICIENT, Disclosure.NOTHING,
         detail_keys=("analysis",)),
    Code("analysis.sampling.too_few_independent",
         "The trajectory holds too few independent samples to support the "
         "claim that was asked for.",
         Kind.INSUFFICIENT, Disclosure.FIELD_ONLY,
         detail_keys=("independent", "frames", "statistical_inefficiency",
                      "needed")),
    Code("analysis.sampling.correlation_unresolved",
         "The run is not long against its own correlation time, so the "
         "independent-sample count is an upper bound.",
         Kind.INSUFFICIENT, Disclosure.FIELD_ONLY,
         detail_keys=("frames", "independent", "statistical_inefficiency")),
    Code("analysis.sampling.drifting",
         "The segment means move in order across the run, so it had not "
         "settled at the scale of the whole run.",
         Kind.INSUFFICIENT, Disclosure.FIELD_ONLY,
         detail_keys=("drift_p", "heterogeneity", "span", "segments")),
    Code("analysis.sampling.not_equilibrated",
         "No equilibrated region was detected, so there is nothing to "
         "average over.",
         Kind.INSUFFICIENT, Disclosure.NOTHING,
         detail_keys=("frames",)),

    # -- batch and report ---------------------------------------------------
    Code("batch.sweep.invalid",
         "A parameter sweep that does not describe a set of runs.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("option", "reason")),
    Code("batch.sweep.empty",
         "A sweep whose cross-product holds no runs.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("options",)),
    Code("report.region.invalid",
         "A requested report region does not describe a residue range this "
         "study holds.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("index", "start", "end", "residues")),
    Code("report.format.unavailable",
         "A report format was asked for that this environment cannot write.",
         Kind.ENVIRONMENTAL, Disclosure.ACTION,
         detail_keys=("format", "install_command")),

    # -- remote -------------------------------------------------------------
    Code("remote.machine.unknown",
         "A machine was named that has no record on this computer.",
         Kind.STRUCTURAL, Disclosure.PERMITTED_VALUES,
         detail_keys=("given", "permitted")),
    Code("remote.machine.unusable_name",
         "A machine name that cannot be passed to ssh as a destination "
         "safely.",
         Kind.STRUCTURAL, Disclosure.FIELD_ONLY,
         detail_keys=("given",)),

    # -- last resort --------------------------------------------------------
    # A raise site that has not been classified yet. Present so that
    # `Refusal` is total -- every refusal carries a code from the day this
    # module lands, and the migration is visible as this code's count
    # falling rather than as an absence nothing measures.
    Code("unclassified",
         "A refusal that has not been given a code yet.",
         Kind.SEMANTIC, Disclosure.NOTHING),
)


#: Codes that have been renamed, old id -> new id. Empty today; present so
#: that the first rename has an established way to happen. `resolve` reads
#: it, so a caller written against an old identifier keeps working and can
#: be told what to move to.
SUPERSEDED: dict[str, str] = {}


_BY_ID: dict[str, Code] = {c.id: c for c in CODES}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
def known(code_id: str) -> bool:
    """Whether this identifier is in the registry, before or after rename."""
    return code_id in _BY_ID or code_id in SUPERSEDED


def resolve(code_id: str) -> str:
    """The current identifier for a possibly-superseded one."""
    seen: set[str] = set()
    while code_id in SUPERSEDED and code_id not in seen:
        seen.add(code_id)
        code_id = SUPERSEDED[code_id]
    return code_id


def code_for(code_id: str) -> Code:
    """The :class:`Code` for an identifier.

    Raises
    ------
    KeyError
        If the identifier is not registered. Deliberately loud: an
        unregistered code in a raise site is a defect in this package,
        not a condition a user can cause, and the test suite holds every
        raise site to the registry.
    """
    return _BY_ID[resolve(code_id)]


def codes_under(prefix: str) -> tuple[Code, ...]:
    """Every code in a family. ``codes_under("setup.chemistry")``.

    A bare phase works too, and an exact id returns just itself.
    """
    if prefix in _BY_ID:
        return (_BY_ID[prefix],)
    dotted = prefix.rstrip(".") + "."
    return tuple(c for c in CODES if c.id.startswith(dotted))


def __iter__() -> Iterator[Code]:  # pragma: no cover - module-level sugar
    return iter(CODES)


# ---------------------------------------------------------------------------
# The refusal itself
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Refusal:
    """A refusal, as a value rather than as an exception.

    Carried by every coded exception and written into the manifest, so the
    same record serves a caller catching in-process and a reader opening
    the run afterwards.

    ``details`` holds the particulars the message interpolated -- the
    option's name, the set of permitted values, the residue, the counts.
    They are the same facts the sentence states, in a form that does not
    have to be parsed back out of it.

    ``permitted`` is a property rather than a field because it is read far
    more often than anything else in ``details``: it is the whole content
    of a ``PERMITTED_VALUES`` disclosure.
    """

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", resolve(self.code))

    @property
    def spec(self) -> Code:
        """The registry entry."""
        return code_for(self.code)

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def retryable(self) -> bool:
        return self.spec.retryable

    @property
    def permitted(self) -> tuple[Any, ...] | None:
        """The legal set, where the registry says it may be disclosed.

        ``None`` where it may not be, *even if the details happen to hold
        one*. The gate is the registry rather than the raise site, so a
        raise site cannot widen a disclosure by accident.
        """
        if self.spec.disclosure != Disclosure.PERMITTED_VALUES:
            return None
        value = self.details.get("permitted")
        if value is None:
            return None
        return tuple(value)

    def matches(self, prefix: str) -> bool:
        """Whether this refusal is the given code or inside its family."""
        target = resolve(prefix)
        return self.code == target or self.code.startswith(target + ".")

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Refusal":
        """Read back what :meth:`as_dict` wrote.

        ``as_dict`` deliberately writes more than this takes: kind,
        disclosure and retryable travel with the record so a reader a year
        from now does not need this module's current registry to interpret
        it. They are derived here, so reading them back would be reading
        stale copies of facts the registry already holds.

        So they are dropped rather than passed through. If the registry has
        since reclassified a code, the reconstructed refusal reflects what
        the code means now, and the record on disk still says what it meant
        then. Both are correct answers to different questions.
        """
        return cls(
            code=str(record.get("code", "unclassified")),
            message=str(record.get("message", "")),
            details=dict(record.get("details") or {}),
        )

    def as_dict(self) -> dict[str, Any]:
        """The form written into a manifest.

        The registry's classification travels with it. A reader opening a
        run a year later should not need this package's version of this
        module to know that a refusal was environmental and retryable.
        """
        spec = self.spec
        return {
            "code": self.code,
            "message": self.message,
            "kind": spec.kind,
            "disclosure": spec.disclosure,
            "retryable": spec.retryable,
            "details": dict(self.details),
        }


# ---------------------------------------------------------------------------
# Exceptions that carry one
# ---------------------------------------------------------------------------
class CodedError(Exception):
    """Mixin giving an exception a :class:`Refusal`.

    Mixed into the existing exception classes rather than replacing them.
    Every one of them keeps the base it had -- ``ConfigError`` is still a
    ``ValueError``, ``LigandError`` still an ``Exception`` -- because code
    outside this package catches on those, and a refusal gaining an
    identifier should not change what catches it.

    The message is unchanged too. ``str(exc)`` returns exactly what it
    returned before, so the 152 test modules and the corpus's
    ``mentioning`` checks keep measuring what they measured.

    Usage at a raise site::

        raise ConfigError(
            f"Unknown {context} option {key!r}{suggestion}. "
            f"Valid options: {', '.join(sorted(valid))}.",
            code="config.option.unknown",
            option=key, context=context, permitted=sorted(valid),
        )

    The prose stays first and stays whole: it is what a person reads, and
    it should not be assembled from the details by a formatter that does
    not know which particulars matter. The details repeat what the
    sentence says, for a reader that is not a person.
    """

    #: What a subclass raised without an explicit code is. Overridden by
    #: subclasses whose every raise site means one thing -- there is no
    #: sense in making `ProtonationError` say so at each of its eleven.
    default_code: str = "unclassified"

    def __init__(self, message: str = "", /, *args: Any,
                 code: str | None = None, **details: Any) -> None:
        super().__init__(message, *args)
        chosen = code or self.default_code
        if not known(chosen):
            # A raise site naming a code that is not registered is a defect
            # here, not a user error, and it must not become a confusing
            # failure inside someone's study. Degrade to unclassified and
            # let the test suite be the thing that catches it.
            chosen = "unclassified"
        self.refusal = Refusal(
            code=chosen,
            message=str(message),
            details={k: v for k, v in details.items() if v is not None},
        )

    @property
    def code(self) -> str:
        """The stable identifier. Shorthand for ``.refusal.code``."""
        return self.refusal.code

    def matches(self, prefix: str) -> bool:
        """Whether this is the given code or inside its family."""
        return self.refusal.matches(prefix)


class OutputExistsError(CodedError, FileExistsError):
    """Writing here would overwrite a study that is already there.

    A refusal rather than a prompt, and rather than a silent overwrite.
    The thing at risk is somebody's completed run, and the cost of being
    wrong is asymmetric: refusing costs a caller one more argument,
    overwriting costs whatever the run took to produce.
    """

    default_code = "environment.path.exists"


class MissingPathError(CodedError, FileNotFoundError):
    """A file the study named is not on disk.

    The counterpart to :class:`MissingResultError`, and the distinction is
    the whole reason both exist. This is a path the user gave that is not
    there, and the remedy is to correct the path. That one is an analysis
    looking for the output of a phase that did not run, and the remedy is
    to run the phase.

    Both were ``FileNotFoundError`` and indistinguishable, so a caller
    seeing one could only guess which had happened -- and guessing wrong
    means either editing a correct config or re-running a phase that was
    never the problem.
    """

    default_code = "environment.path.not_found"


class MissingResultError(CodedError, FileNotFoundError, RuntimeError):
    """What an analysis reads was never produced by this study.

    Distinct from a file the user named that is not on disk. This is an
    analysis looking for the output of a phase that did not run, or ran
    and refused: no `pmf.json` beside an umbrella study, no state record
    beside a run that stopped in setup.

    The remedy is a phase, not a path, and for an automated caller that is
    the difference between "fix what you asked for" and "run the thing
    this depends on first".

    Subclasses ``FileNotFoundError`` and ``RuntimeError`` because the sites
    it replaces used one or the other with no principle behind the choice:
    a missing `pmf.json` raised ``FileNotFoundError`` in `analysis/` while
    a missing prepared system raised ``RuntimeError`` in `simulation/`.
    Inheriting only the first narrowed what caught the second, and the
    graceful-degradation path in the simulation pipeline stopped catching
    it -- found by the suite, which is the argument for the suite.

    Widening never breaks a handler and narrowing silently does, so where
    a class replaces two builtins it inherits from both.
    """

    default_code = "analysis.data.absent"


class BackendDefect(CodedError, RuntimeError):
    """An optional backend is installed, and is returning wrong answers.

    Distinct from :class:`BackendUnavailable`, and the distinction decides
    what a reader should do. An absent backend is installed. A defective
    one cannot be, and the remedy is another platform or another method.

    ``RuntimeError`` because that is what these sites raised, and because
    it is what they mean: nothing about the study is wrong and nothing a
    caller can write will fix it.
    """

    default_code = "environment.backend.defective"


class BackendUnavailable(CodedError, ImportError, RuntimeError):
    """An optional backend is not installed, or would not load.

    Subclasses both ``ImportError`` and ``RuntimeError`` because the sites
    it replaces raised one or the other, arbitrarily: SciPy missing was an
    ``ImportError`` in one module and a ``RuntimeError`` in another, for
    no reason either module could state. Inheriting from both means every
    existing ``except`` keeps catching, whichever one it named.

    Widening what catches an exception is safe in a way narrowing is not.
    Nothing that handled these before stops handling them; a handler that
    was written for one of the two now also sees the other, which is what
    it would have wanted in the first place.

    Distinct from ``MissingBackendError``, which is raised up front for a
    whole phase and carries the install command for every dependency at
    once. This is for a single backend discovered missing partway through.
    """

    default_code = "environment.backend.missing"


class UnstableRun(CodedError, RuntimeError):
    """The integration produced a state that is not a physical one.

    Positions that are NaN, an energy that is infinite, an integrator that
    stopped. Raised through one helper in the runner, which is why this
    class rather than a code at each site: eleven raise sites share a
    single constructor, and coding the constructor codes all of them and
    keeps them consistent by construction.

    Semantic rather than structural, and the reason is worth stating
    because it decides what an automated caller should do with it. A
    blown-up run is not a config that can be repaired by consulting the
    schema. It may be a timestep, or a temperature, or a ligand whose
    parameters are wrong, and no timestep is small enough for the last of
    those. The software does not know which, so `Disclosure.NOTHING`
    applies and the remedy belongs to whoever reads the diagnosis.
    """

    default_code = "simulation.run.unstable"


class CodedKeyError(CodedError, KeyError):
    """A lookup that failed, with a code on it.

    ``KeyError`` rather than ``StudyError`` because these are registry
    lookups -- an analysis by name, a colour by role -- and code that
    treats a registry as a mapping catches ``KeyError``. Widening those
    into ``ValueError`` would be the narrowing mistake in reverse: the
    handler that was written stops running, and nothing says so.

    One wrinkle worth knowing. ``KeyError.__str__`` quotes its argument,
    so ``str(exc)`` on one of these has quotation marks around it that a
    ``ValueError`` would not add. That is ``KeyError``'s behaviour and not
    something to paper over; ``refusal.message`` holds the sentence
    unquoted for anything that needs it clean.
    """

    default_code = "unclassified"

    def __init__(self, message: str = "", /, *args: Any,
                 code: str | None = None, **details: Any) -> None:
        super().__init__(message, *args, code=code, **details)


class StudyError(CodedError, ValueError):
    """A refusal with no more specific class to be.

    Two hundred and twenty raise sites in this package say ``ValueError``.
    Most of them are refusals in the ordinary sense -- a variable that
    takes no selection, a cutoff larger than half the box, a lipid the
    force field cannot describe -- and they say ``ValueError`` because
    that is what Python offers, not because anything about them is about
    a value's type.

    A bare ``ValueError`` cannot carry a code, so this exists to be the
    thing those sites raise instead. It subclasses ``ValueError``, so
    every ``except ValueError`` that catches them today keeps catching
    them, and every message is unchanged.

    Prefer a specific class where one exists. ``LigandError`` says
    something this does not, and a site that has a home should go to it.
    This is for the sites that have none.
    """

    default_code = "unclassified"


def refusal_of(value: Any) -> Refusal:
    """The :class:`Refusal` for anything that carries one.

    Total by construction: an exception from a raise site that has not been
    migrated, or from a dependency, comes back as ``unclassified`` carrying
    its own message. So a caller can be written against refusals today and
    the migration improves its resolution rather than switching it on.

    Not restricted to exceptions, because not every refusal is raised.
    :func:`fastmdxplora.statistics.summarise` withholds a mean by returning
    the reason, and an analysis records one under a ``refused`` key rather
    than stopping the phase. One reader for all three is the point: a
    caller should not need to know which mechanism a given refusal
    happened to use.
    """
    found = getattr(value, "refusal", None)
    if isinstance(found, Refusal):
        return found
    if isinstance(value, dict) and value.get("refused"):
        return Refusal(code="unclassified", message=str(value["refused"]))
    return Refusal(code="unclassified", message=str(value),
                   details={"exception": type(value).__name__}
                   if isinstance(value, BaseException) else {})
