# V4 — experimental anchor: RMSF against crystallographic B-factors

**Written 2026-09-06T04:12Z.** The 100 ns ubiquitin run began at
2026-09-05T23:34:06Z and was at 61.8 of 101.5 ns when this was written; its
analysis phase had not run and no correlation existed anywhere. This is
therefore before the result, which is what a pre-registration has to be —
and not before the run, which it does not. Stated plainly rather than
implied.

## The comparison

Per-residue Cα RMSF from the trajectory, against the RMSF implied by the
deposited B-factors of 1UBQ through ⟨u²⟩ = 3B/8π². Both are computed by
`bfactor_comparison`, which writes `bfactor_comparison.dat` with one row per
residue: residue number, simulated RMSF (nm), implied RMSF (nm). Every
figure below is recomputable from that file by anyone who has it.

## Pre-registered

**Statistic: Spearman ρ.** Pearson *r* is reported alongside it, without a
threshold.

Rank rather than value, because the two quantities are not the same
measurement. A refined B-factor carries static disorder across the lattice,
the refinement's restraints and its TLS or occupancy model, along with
thermal motion; the analysis says so in its own `what_this_is_not` field.
A linear correlation between quantities that are not the same measurement
invites a regression slope that means nothing. The rank correlation asks the
question the comparison can answer: **are the flexible residues the same
residues.**

**Residue set: 1–72. Residues 73–76 excluded.**

Ubiquitin's folded β-grasp domain is residues 1–72; 73–76 (Leu-Arg-Gly-Gly)
is the flexible C-terminal tail that conjugates to substrates. Its
crystallographic disorder and its solution mobility are different phenomena,
so a B-factor there reports weak density more than thermal motion. The
criterion is structural and fixed by the protein, not by either dataset.

**This exclusion makes the test harder, not easier**, which is the reason it
can be trusted. The tail is genuinely the most mobile region on both sides,
so it would be correctly ranked and would *raise* ρ. Removing it removes the
easiest agreement in the comparison.

**Threshold: ρ ≥ 0.6.**

Stated as the judgement it is, and set without appeal to any published
acceptance range. Below 0.6 the two orderings disagree enough that the
simulation and the crystal are not identifying the same flexible regions;
above it they are — which is all this comparison establishes, and
specifically not that the amplitudes agree.

The number is not derived from anything. It is written down here, before the
result exists, so that it is a commitment rather than a description.

## Expected direction of disagreement

**The simulation should exceed the crystal**: mean simulated RMSF greater
than mean implied RMSF over residues 1–72, so `mean_simulated_nm /
mean_implied_nm > 1`. Lattice contacts damp the loop excursions a solution
trajectory is free to make, so B-factors bound loop amplitudes from below.

Secondarily, the largest positive deviations should fall in loops rather
than in secondary structure — the regions around 8–11, 30–36 and 46–48,
named in advance from the ubiquitin dynamics literature rather than from
this run.

**The informative failure is the opposite sign.** A simulation
systematically *below* the crystal over the folded domain would indicate
dynamics that are over-restrained, and that would be a finding about the
pipeline rather than about crystal packing. It is written here so that it
cannot be explained afterwards.

## Reported either way

Both correlations, the residue count, the number of residues that could not
be matched, both means and their ratio, and — if ρ falls below 0.6 — which
residues carry the disagreement and whether they are loops. The result is a
property of the force field as implemented, not a pass or fail criterion for
the framework.

## Not in this experiment

The N–H order-parameter comparison against NMR was withdrawn on
2026-09-06: no per-residue experimental set for wild-type human ubiquitin is
publicly retrievable, and a reference a reader cannot obtain is not one this
study will use.
