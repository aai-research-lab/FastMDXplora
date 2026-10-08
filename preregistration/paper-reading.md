# Reading a paper's MD studies: pre-registered

**Written 2026-10-08, before any AI model's reading of these papers through
FastMDXplora was seen.** The harness is `fastmdxplora.validation.paper_reading`;
this file fixes which papers are read, what the truth is, how a reading is
counted and what is claimed from the counts.

## The papers

Twenty, listed in `PAPERS` with their DOI, how each is reached and its
licence. They were chosen by two researchers working apart, each searching
for papers that test one part of the feature, and kept where both lists
agreed or the reason was strong:

- methods stated in full, and methods with gaps (settings by reference to
  another paper, in the supporting information, or not at all);
- force fields and water models this software runs (ff14SB, ff19SB with OPC,
  CHARMM36m, bsc1, OL15, OL21, Lipid21, CHARMM36 lipids) and ones it does
  not (OPLS-AA, GROMOS, CHARMM22\*, TIP4P-D, Tumuc1, Martini, Drude);
- methods it runs (plain MD, umbrella sampling, OPES, metadynamics) and ones
  it does not (milestoning, bias-exchange metadynamics, replica exchange,
  simulated tempering, alchemical free energies, QM/MM);
- mutants, ligands, membranes, nucleic acids and peptides, and one dataset
  paper with one protocol for many systems;
- results with errors of the mean, with spreads, and with none;
- nineteen open access, so they are fetched by DOI, and one free to read but
  not licensed for programs (Buch 2011), which the feature must refuse to
  fetch and read from its PDF when given one.

## The truth

Each paper was read by two readers working apart, from its full text, into
the fields of `fastmdxplora.paper.fields` (each `stated` with its value and
unit, `by_reference`, `in_si` or `not_stated`), and its reported results
with their errors. The readers had one brief and did not see each other's
work. `reconcile()` pairs their studies by what tells studies apart and keeps
as the truth:

- the studies both found (245; one reader found 287, the other 247);
- each field both state with one value, or both say is given the same other
  way, as agreed (9,431 fields, 4,892 of them stated values); the rest are
  contested (369) and are not counted;
- each result both list with one value (525).

The truth keeps values, units and where in the paper each is, never the
paper's words. It is in `paper-reading/truth/`, one file per paper, and can
be made again from the two readers' folders with `--reconcile`.

## How a reading is counted

The feature reads each paper as a person would have it read: fetched by its
identifier (Buch 2011 from its PDF), read by the AI model chosen, each value
checked against the paper's words, nothing kept from an earlier reading.
`score()` pairs the reading's studies with the truth's by what they share
(a value given differently does not keep two studies apart here, so a
study read with wrong values is counted, not left unpaired), and for each
agreed field of each paired study counts:

| Count | When |
|---|---|
| correct | the truth states it and the value used is the truth's |
| wrong | the truth states it and another value was used |
| missed | the truth states it and no value was used |
| invented | the truth says the paper does not give it, and a value was used |
| agreed absent | neither |
| elsewhere | the truth says the paper gives it by reference or in its supporting information; not counted, since a reading given the supporting information may find it there and the truth does not know its value |

A study the reading has and the truth does not is counted too: each value it
uses is **invented**, unless one of the two readers found it (the truth lists
those by their labels, and a study whose label shares half its words with
one is not counted).

A value is used when the feature's check holds it (status `stated`); a value
the AI model gave that the paper's words do not hold is not used, and counts
as missed or agreed absent. Numbers are one value within a thousandth after
conversion to one unit; names are one value when they name the same force
fields, water models, thermostats and so on; mutations in any of their
written forms. Eight fields are descriptions rather than choices (`system`,
`structure_source`, `ligands`, `protonation`, `minimization`,
`equilibration_restraints`, `method_details`, `chains`) and are not counted,
since two wordings of one description are not told apart well. Each agreed
result is counted as found when the reading lists the same quantity with
the same value.

## What is claimed

One reading of each paper with the AI model the run names. Pooled over the
papers read:

1. **Values used are right.** Of the values used for agreed fields, those
   correct are at least 98%: correct / (correct + wrong + invented) ≥ 0.98.
   This is what the feature's design rests on: a value is used only where
   the paper's words hold it, so a wrong value in a config should be rare.
2. **Most stated values are found.** Of the values the truth states, those
   correct are at least 75%: correct / (correct + wrong + missed) ≥ 0.75.
3. **Most studies are found.** Of the truth's studies, those paired with one
   of the reading's are at least 80%. The share of the reading's studies
   that are the truth's is reported beside it.
4. **Buch 2011 is refused by its DOI** with `environment.paper.not_open`, and
   read from its PDF when given one.

Each is reported as met or not met by the harness (`claims_met()`), with
the counts, whatever they are; a claim not met is said in `docs/papers.md`
as it stands. Counts per field and
per paper are reported too, but no threshold is set for them. Results found
are reported and not claimed.

A check of the harness itself, before this registration: each reader's own
readings, passed through the feature's check and counted against the truth,
gave 3,873 correct, 0 wrong, 127 missed and 0 invented (the first reader)
and 3,723, 0, 277 and 0 (the second). The missed are values the reader's
quote did not hold: a production length summed from several, a mutation
named in another sentence than the one quoted, a count of copies said only
in words like "monomers". Each reader made half the truth, so this is a
check that the counting works, not a result.

## Amended before any reading

On 2026-10-08, after two reviews of the harness and before any AI model's
reading of these papers through it, three rules above were added, none a
threshold: studies are paired without a penalty for values given
differently (a reading with every value wrong was left unpaired and counted
nothing wrong); a study the truth does not have counts its values as
invented, unless one reader found it; a field the truth places by reference
or in the supporting information is not counted. The thresholds are as
first written.

## How to run it

With an AI model chosen (`fastmdx agent model`) and the internet:

```
python -m fastmdxplora.validation.paper_reading --out paper_reading.json \
    --file buch_2011=/path/to/buch_2011.pdf
```

`--papers` takes keys, comma separated, to read only some.
