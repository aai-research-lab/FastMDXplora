# The Agent's evaluation, version 2: pre-registered

**Written 2026-10-13, before any reply from an AI model to the cases it
adds was seen.** The harness is `fastmdxplora.validation.agent_eval`, set
`agent-eval`, version 2. Version 2 is version 1 (`agent-eval.md`) with one
kind added, `context`: every case of version 1, its messages, what it must
come to, how it is judged and what is claimed of it, stays as registered
there, and a recording of version 1 replays under version 2. This file
fixes what the new kind asks, how it is judged and what is claimed of it. A
change to any of them after the first reply is seen is a new version.
Results are added under **Results** at the end, as found, and nothing above
that heading changes with them.

## Why

The Agent now knows more of where the person is (the round `agent-context`):
the page they are on and what is open in it, what they name with @ in a
message, and the turns before the last twelve, kept as what was decided
and what is still open rather than by each turn's first sentence. Each of
these is meant to make a reply right that was wrong without it. Version 1
cannot show it: none of its cases has a page, an @, or more than twelve
turns. The `context` kind asks for exactly what each part adds, and is
asked twice, with the Agent's context and without it, so what it adds is
counted against the same AI model on the same day.

## How a case is asked

As in version 1, with these additions for a `context` case:

- **Earlier turns.** The turns the case lists come before its message, in
  the conversation as the page keeps it (a question with its candidates on
  a line `Candidates: ...`). Each far case has sixteen: four that settle or
  ask something, then twelve about the software (`_ASIDES`), so what was
  settled is no longer among the last twelve kept whole.
- **The page.** Where the case lists the page's hints, they are given to
  the look at what the page shows (`current_view`), `{study}` standing for
  the finished study given, and the open study is that study.
- **The folder of studies.** With a study given, the folder it is in is
  the folder of studies, as the GUI started there would have it.

**With the Agent's context** (the default), each message is asked as the
page asks it on the branch that adds it: the earlier turns kept as decided
and open, what is named with @ resolved to what it names, and the page's
hints whole.

**Without it** (`--without-context`), the Agent as it is on that branch
with these three parts left out: the earlier turns each by its first
sentence, as before; nothing named with @ resolved (the @ is in the message
as typed); and the page's hints only its name and the study open, which is
what the page sent before from every page while nothing had been shown in
the Viewer (once something had, its frame and view went too). The rest of
the branch (the plan, the look at a study's file) is in both arms.

Both are asked by the same AI model, the one the Agent is set to, three
times each, on the same day, the one after the other.

A recording replays as version 1's do. What is named with @ is resolved
from the folders on the machine replaying it, so where the study is not
there the requests of the two @ cases are counted as asked differently;
their verdicts replay all the same.

## The cases

Eight, in three groups. The four that read a finished study are asked only
where one is given (`--study`), as version 1's `records` cases are.

### What was settled many turns before (judged as `write`)

- `ctx_far_forcefield`: earlier "I want to simulate ubiquitin, 1UBQ, in
  water.", the Agent asking "Which force field should it use?" with the
  candidates amber14 and charmm36, then "Thanks, that helps. Use charmm36
  for the force field.", and "Understood. I will use it when I write the
  study."; the message "Now write the study for it, 10 ns." Must hold
  `setup.forcefield` charmm36 and `simulation.duration_ns` 10, of 1UBQ.
- `ctx_far_temperature`: earlier "I will be running trp-cage studies this
  week.", "Good. Tell me what each should hold and I will write it.",
  "Fine. Every run should be at 310 K, not the default.", "Understood. I
  will keep to that."; the message "Write the study for 1L2Y, 5 ns." Must
  hold `simulation.temperature_K` 310 and `simulation.duration_ns` 5, of
  1L2Y.
- `ctx_far_ph`: earlier "I am reproducing a paper's ubiquitin runs.",
  "Which conditions does the paper use?", "It is a standard setup. The
  protonation is set at pH 6.0, as the paper did.", "Understood. I will set
  that when I write it."; the message "Write it for 1UBQ, 10 ns." Must hold
  `setup.ph` 6.0 and `simulation.duration_ns` 10, of 1UBQ.

In each, what decides the study is in the second sentence of the person's
turn, and the Agent's turns after it do not repeat it, so the first
sentence of each turn does not hold it.

### A question still open (judged as `ask`)

- `ctx_open_question`: earlier "Simulate lysozyme in water.", the Agent
  saying "There are two common lysozyme structures in the PDB. Hen
  egg-white (1AKI) or T4 (2LZM): which one should it be?" with the
  candidates 1AKI and 2LZM, then
  "Before that, does the Agent keep my conversations?" and "Yes. Each is
  kept with the study it is about, or in your settings folder where it is
  about none."; the message "Go ahead and write it, 10 ns." Passes as a
  question whose words or choices name 1AKI, 2LZM, hen or T4. A config of
  either is the AI model choosing for the person, and fails. Without the
  context, the Agent's turn is kept as its first sentence, which says there
  are two structures and names neither, nor that a question is open.

### What the page shows, and what is named with @ (judged as `records`)

- `ctx_file_open`: on the Files page, the study open, its
  `analysis/rg/options.json` open in the file reader; the message "What
  mean does the file I have open record?" The mean of `rg`.
- `ctx_report_section`: on the Report page, the study open, its section
  "RMSD" being read; the message "What value does this section give?" The
  mean of `rmsd`.
- `ctx_mention_study`: on the Agent page, no study open; the message "What
  mean radius of gyration did @{study_name} record?", `{study_name}` the
  name of the given study's folder. The mean of `rg`.
- `ctx_mention_analysis`: on the Overview, the study open; the message
  "What did @rmsd come to?" The mean of `rmsd`.

Each asks for the `ANSWER:` line as version 1's `records` cases do, and is
judged as they are.

## How a case is judged

On what the last message came to, as version 1 judges the kind named:

- **write:** as version 1's `write`: an accepted config whose runs hold
  each setting listed and name exactly the structures listed.
- **records:** as version 1's `records`: the reply's `ANSWER:` line, its
  first number within one standard error of the mean the study records (or
  within half a unit of the reply's last place, as there).
- **ask:** a question whose words or choices name one of those listed. An
  accepted config fails, as does an answer, an action or nothing accepted.

The rest is as version 1: a trial the provider fails is asked again twice
at most and is not judged if it never completes; any other error fails it.

## What is claimed

Counted per trial, each arm 24 trials (the eight cases three times), the
twelve that read a study only where one is given. Where trials a claim
names were not judged, the claim is not decided unless those judged decide
it.

1. **With the Agent's context, at least 18 of the 24 `context` trials
   pass.**
2. **The context adds at least 6:** with it, at least 6 more of the 24
   trials pass than without it, asked by the same AI model on the same
   day.
3. **No choice made for the person:** with the Agent's context, none of the
   3 `ctx_open_question` trials comes to a config.

The thresholds were set by judgment, before any reply was seen: three
trials in four for the kind, and a gain of one case in four for what the
context adds. The two cases named with @ can pass without the context too
(a study's folder can be listed, and the page always named the study open),
so claim 2 rests mostly on the other six.

**Checked, not claimed.** Each case's counts with and without, side by
side; which looks were taken; whether `current_view` was the look that read
the file or the section.

**What is not measured here.** The plan kept as a checklist (it does
nothing, and is held by the suite's tests); what the Analysis page, the
Config Builder and the Viewer's selection add, whose hints come in a later
round (`agent-context-2`) and are for a later version; whether a resolved
@ helps where the AI model would have found the record anyway.

## Running it

On a machine with an AI model chosen, from the branch that adds the
context, with a finished study whose RMSD and Rg means are determined:

```bash
python -m fastmdxplora.validation.agent_eval --kind context --repeats 3 --study <the study> --out agent_eval_context.json
python -m fastmdxplora.validation.agent_eval --kind context --repeats 3 --study <the study> --without-context --out agent_eval_context_without.json
```

Bring both files back: they are reported under **Results** and kept as
recordings the suite replays.

## Results

### The `context` kind with and without the Agent's context, 2026-10-13

Asked on a Mac with `anthropic/claude-opus-5-5`, tool calls, three repeats,
from the round's two commits applied on `main` at `e81bd023` (tree
`19b1676a`, checked there; the run names the local commit, `c4c69a5d`),
against `fastmdxplora_1l2y_study_20260930003505`, the study version 1's
records trials were asked again against (Rg 0.7184 nm, RMSD 0.0645 nm, both
determined). With the context at 22:03 UTC, without it at 22:06 UTC: 24
trials each, every one judged, none asked again for the provider. All three
claims are met.

| Claim | Counted | Met |
|---|---|---|
| 1. With the context, at least 18 of 24 | 24 of 24 | yes |
| 2. At least 6 more than without | 24 against 9: 15 more | yes |
| 3. No config for the question still open | 0 of 3 came to a config | yes |

| Case | With | Without |
|---|---|---|
| `ctx_far_forcefield` | 3 | 0 |
| `ctx_far_temperature` | 3 | 0 |
| `ctx_far_ph` | 3 | 0 |
| `ctx_open_question` | 3 | 3 |
| `ctx_file_open` | 3 | 0 |
| `ctx_report_section` | 3 | 0 |
| `ctx_mention_study` | 3 | 3 |
| `ctx_mention_analysis` | 3 | 3 |

**What was decided many turns before.** Without the context, each of the 9
trials wrote an accepted config of the structure and length asked, with the
setting decided earlier left at its default. In all three
`ctx_far_forcefield` trials the reply told the person they had not chosen a
force field (in one, "You didn't pick a force field when I asked earlier"),
where their turn chose charmm36 in its second sentence. With the context,
all 9 held it.

**What the page shows.** In both arms the Agent looked at the page first
(`current_view`, all 12 trials). Without the context it was told the page
and the study only, and in 5 of 6 asked which file or section was meant;
in the sixth it listed the study's means and answered "unknown, until you
name the section". With the context, the look named the file open and
gave it whole, and the answer was read from it (3 of 3). On the Report
page it named the section; the Agent then asked for a file of the RMSD
analysis that the study does not hold (`analysis/rmsd/summary.json` twice,
`results.json` once, each refused as no such file) and answered from the
study's record (3 of 3).

**What the passes hide: looks cut short.** Each look gives the Agent at
most 4,000 characters. With the context, the study's record (`read_study`)
is cut in the last words of its `rmsd` entry, as each analysis not
determined before it says why at length. The entry says nothing more of the
mean (it is determined), but the Agent could not know that, and the entries
after it (`sasa` and on) are not given. The look at the Report page names
the section but does not give its text: it gives the study's record, cut
before its `rmsd` entry. In 5 of the 6 trials with the context that quote
the RMSD mean (the 3 `ctx_report_section` and 2 of the 3
`ctx_mention_analysis`) the reply said the record it read was cut off. In
one of them it said the error should stand, which is so, but not from
anything it was shown. The third `ctx_mention_analysis` trial read
`analysis/rmsd/options.json` to see past the cut. A question about an entry
after `rmsd` would not have passed. On the Files page the file open (518
bytes) comes first in the look, whole.

**The question still open, and the names with @,** passed in both arms, as
the claims allowed for. Without the context the Agent asked about the
lysozyme all the same, giving as its reason that hen egg-white and T4
lysozyme are different proteins, and in two of three said the earlier
question was never answered; in all three it searched the PDB and offered
2VB1, 1LYZ, 2LZM and a human lysozyme (2NWD twice, 1LZ1 once), never 1AKI.
With the context it asked in all three, but kept to the two candidates it
had given only once: in the other two it searched the PDB and offered other
structures beside them (2VB1, 1LYZ, 1LZ1), and in one of those 1AKI was not
among the choices. The case passes as registered, on a question naming the
candidates; what was offered before was not held to. For the @ cases,
without the context the Agent read the study named by its folder's name
(`ctx_mention_study`), or listed the studies and looked at the page for the
open one (`ctx_mention_analysis`); with it, it read the study the @
resolved to.

**Checked, not claimed.** `current_view` was the first look in each of the
6 page trials with the context, and the look that gave the file or the
section. `keep_plan` was not called in either arm: no case asks for steps.

**Read, not findings.** With the context, 47 requests (tokens read from
the cache 957,181, written to it 34,451, not cached 142, output 11,916),
and looks `read_study` 9, `current_view` 6, `find_structure` 4,
`read_study_file` 4 (3 of them the files refused above). Without, 43
requests (885,624, 14,097, 134, 10,036), and looks `current_view` 9,
`find_structure` 5, `read_study` 3, `list_studies` 3. All 18 configs
written were accepted.

**What was changed to keep them in the suite**
(`tests/data/agent_eval/context_2026-10-13.json.gz` and
`context_without_2026-10-13.json.gz`): the home folder of the person who
ran them is written `/Users/person`, and in the run without the context,
the three looks listing the folder of studies (`ctx_mention_analysis`) keep
only the entry of the study asked about: each other entry, with its tags
and notes, is written "(a folder of the person's, not kept in this
recording)". Nothing else. Each replays to every verdict recorded. Replayed
on the branch, the 12 trials of the far cases and the question still open
in each run (and without the context, `ctx_mention_study`'s 3) are asked
exactly as recorded, every request and look. In the others, 38 exchanges
with the context and 11 without are counted as asked differently: each is a
look whose request names the study's folder, or a request after a look
whose text names it, and differs by what was written anew (the home folder,
the folders listed) after the run's digests were taken. The first request
of the two @ cases with the context differs only for the reason given
above: what an @ names is resolved from the folders on the machine
replaying. The suite holds the verdicts and, as for version 1's run, a
digest of what is sent (the Agent's own words and the config schema's
structure, not its prose); it reports these counts and does not hold them.
Once what is sent changes, a replayed verdict shows that the same replies
are judged the same, not what the Agent would reply now. Their
`fastmdxplora` version, `2.5.9.dev475+gb091679ad`, is the editable
install's, as in version 1's run.

### What is sent, changed after the run (`agent-looks`, 2026-10-14)

`read_study` takes the analysis asked about and gives it first, the look at
what the page shows passes the analysis the page is on, an analysis named
with @ says to pass it, and of a reason a mean is not determined (other
than the analysis asked about, whose reasons stay whole), all after its
first sentence is said once under the list of what the analyses found (the
Agent's own context gives the record of the study open the same way). This
is the change made for the looks cut short above. The
suite's three digests of what is sent were set again; every verdict of the
three recordings replays as recorded. The runs stand as recordings of the
Agent as it was asked; whether the change shortens the replies that said
the record was cut is for a run asked again, not claimed here.

### What is sent, changed again (`agent-one-list`, 2026-10-15)

The Agent's looks and `fastmdx mcp`'s tools are built from one list, each
tool defined once. The Agent is offered three looks an AI app had
(`views_of_study`, `list_machines`, `remote_status`); its looks' arguments
are said from their schemas; `check_config` says the plan with your
defaults filled in, looked for from the study's folder up, the study it
carries on and whether this machine can run it; `read_study` gives the
study's record as an AI app is given it, where it stands first. The suite's
three digests of what is sent were set again; every verdict of the three
recordings replays as recorded. Whether the Agent answers better with the
new looks is for a run asked again, not claimed here.

### What is sent, changed again (`core-bounds`, 2026-10-22)

The analyses `include` and `exclude` accept gained `image_check`, the check
of each frame's solute against its own periodic images: a setting's
choices, in the schema's structure. The suite's three digests of what is
sent were set again; every verdict of the three recordings replays as
recorded. Then setup's default padding (`solvent_padding_nm`) became
2.0 nm, where it was 1.0 (about 1.0 nm of water between the solute and the
box's wall, decided 10-18): a setting's default, in the schema's
structure. The digests were set again, and every verdict of the three
recordings still replays as recorded, so the runs stand; what the Agent
would now propose of a box is for a run asked again, not claimed here.
