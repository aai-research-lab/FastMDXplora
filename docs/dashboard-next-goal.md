# Dashboard release checkpoint and next goal

Updated 2026-10-03 against the current branch and latest installed wheel. This
checkpoint orders the remaining work in the
[completion plan](dashboard-completion-plan.md); it does not replace or reduce its
M0–M8 requirements. Detailed implementation and test receipts remain in the
[status record](dashboard-feature-status.md).

## Current state

Work is in `C:\Users\User\OneDrive\Documents\GitHub\FastMDXplora`, branch
`context-aware-agent`, tracking `princeote/context-aware-agent`. Current revision
is `201cc3686216eb45a5e9ae6807cb0f20c900dc2f`; the checkout was clean and the
remote tip matched when checked. The development wheel is
`2.5.9.dev207+g201cc3686`, SHA256
`2f488a5e7970cb526ed5dfcfbf256f2ed131a886764e6d61affc4e0a9ec45aab`. It is
installed in an isolated acceptance environment, passes `pip check`, and includes
all 127 packaged error references and required UI assets. The completed 1L2Y
study loads there. The user's separate port 8783 dashboard was preserved.

| Milestone | What exists | What remains |
| --- | --- | --- |
| M0 | Branch scope, architecture, scientific boundaries and requirement map are reconciled | Keep exact-revision evidence current through final handoff |
| M1 | Persistent Agent, eight context types, packaged Markdown, context inspector/opt-out, draft diff and human review guards | Latest installed integrated acceptance, including disabled/stale/error states and exact review invalidation |
| M2 | Study-local bookmarks with screenshots, titles/tags/notes, dates, search, import/export and source-aware restoration | Latest installed restart/import/restore flow, including stale and missing sources |
| M3 | OpenAI/Claude/Kimi/Gemini adapters, provider dropdown, requested Codex order and reasoning controls; live OpenAI inference already succeeded | Recheck connected OpenAI Agent route/model/reasoning after restart; other provider live gates remain unavailable without accounts |
| M4 | GIF/MP4/Both, frame range/stride/fps, rotation, labels, resolution, preview/sidecar and grouped controls | Latest installed preview/download/hash and cancel/failure restoration as an integrated flow |
| M5 | Bounded observational provenance; controlled POPC and installed-wheel OpenFF preservation evidence | Reconcile exact fixture/platform coverage; retain the separate CPU repeatability failure and platform/chemistry limits |
| M6 | Optional preparation audit with inventories, affected-residue track, decisions, guarded comparison/overlay and Agent/bookmark links | Latest installed selection, ambiguity refusal, overlay and bookmark restoration |
| M8 | Visual foundation, three themes, dark dropdowns, responsive panels, text scaling, keyboard splitters and latest mobile toolbar fix | Installed native review of all listed surfaces/themes/widths/expanded states, including 200% text and focus/contrast |
| M7 | Latest wheel installs and starts; resource, mobile regression, and earlier feature receipts pass | Freeze one final revision; complete broad visual/integrated checks and final combined suite on it |

The latest mobile-toolbar regression passes in all three themes at 390/768 px and
100%/200% text. It checks actual body scrolling, identity hit-testing, bookmark
opening, Escape and focus return (3 checks, 18.59 s). This source-level regression
and successful wheel startup do not replace the outstanding native review at
those sizes or the full M7 gate.

The Agent API-key error has a known route explanation: the earlier connected
OpenAI session was using the API/local route. Selecting the connected subscription
model restored successful Agent replies without entering or copying an API key.
The final regression must verify the selected route, model and reasoning persist
after restart and a harmless explanation uses the connected OpenAI subscription;
the explicit API-key/local-server action remains separate. Prior error text in the
conversation history is not evidence of a current failure.

The accepted model order remains Astra, Sol 6.1, Sol 6, Luna 6, then the rest.
Do not repeat already-passing model probes without a route change or new failure.
The user has only OpenAI subscription access. Preserve Claude, Kimi Code and Gemini
adapters and report their real-account gates as unverified.

## Revised execution order

1. **Finish latest-build native acceptance.** Use the already installed 201cc
   wheel in the isolated port 8784 review environment. Verify the mobile toolbar
   at 390/768 px and 200% text, current bookmark cards and grouped clip dialog,
   saved OpenAI route/model/reasoning, packaged knowledge and a harmless Agent
   reply. Keep the user's port 8783 session and credentials untouched; avoid
   restarting a live server just because one browser observation fails.
2. **Close M8 with a finite visual matrix.** Review Overview, Config, Analysis,
   Viewer, Agent, Bookmarks, audit, Report, Files and Settings in Graphite, Ink
   and Paper at 1440/1280/1024/768/390 px and 200% text. Cover expanded panels,
   dialogs, warnings, empty/error/stale states, long text, missing images/models,
   native dropdown options, keyboard navigation, Escape and focus return. Record
   reviewed combinations and fix observed defects only. Reset temporary viewport,
   theme and zoom changes after review.
3. **Complete the integrated feature flow.** Check graph/residue identity and
   comparison, server-resolved context and opt-out, exact draft review with no
   autonomous apply/run, bookmark save/restart/import/restore, preview and actual
   GIF/MP4 downloads, decoded dimensions/hash, cancel/failure restoration, and
   audit selection/overlay guards. Verify source hashes; use controlled identity
   fixtures where the historical study lacks unambiguous residue mapping.
4. **Reconcile science and provider boundaries.** Tie each preservation result to
   its exact fixture/backend/platform. Do not change algorithms, force fields,
   protonation, defaults, seeds, atom order, units, analysis definitions or
   numerical tolerances to satisfy a gate. Keep the reproduced baseline CPU
   repeatability failure and aggregate provenance limits separate. Do not ask the
   user to purchase other provider subscriptions.
5. **Freeze and run the final gate.** Stop code/test edits during the required
   combined run. Record every pass, failure, skip and environment. Build/install
   the exact final revision outside the checkout, verify resources/dependencies,
   rerun changed-area checks and perform the installed integrated flow. Any later
   code edit requires affected checks and a new final wheel.
6. **Finish the branch handoff.** Reconcile this checklist and the status map,
   review the final diff for scientific files, update README/changelog/packaged
   knowledge as needed, then commit and push only to `princeote/context-aware-agent`.
   Give a concise account of behavior, evidence and limits. Do not integrate
   upstream main or create/merge a PR unless requested.

Do not add cosmetic scope without a requirement or observed defect. Do not repeat
passing checks without a new change, failure or required final gate. Continue
across milestones; there is no milestone-stop requirement.

## Completion rule

Report local/OpenAI release acceptance separately from whole-framework acceptance.
Missing Claude/Kimi/Gemini accounts are external unverified gates, not synthetic
passes. Scientific platform and chemistry limits remain qualified by their
verified scope. Do not mark the full framework complete unless every required
gate has evidence; if only external gates remain, give a finite dependency
handoff rather than looping on completed work.

## Ready-to-use continuation goal prompt

Continue the active FastMDXplora dashboard completion goal using the current live
state. Work only in
`C:\Users\User\OneDrive\Documents\GitHub\FastMDXplora`, branch
`princeote/context-aware-agent`, currently at `201cc3686216eb45a5e9ae6807cb0f20c900dc2f`.
Follow `docs/dashboard-completion-plan.md`, `docs/dashboard-feature-plan.md`,
`docs/dashboard-feature-status.md` and this checkpoint. Preserve every M0–M8
requirement. Do not work in the Downloads copy or integrate upstream main.

First finish installed native acceptance of wheel
`2.5.9.dev207+g201cc3686` in the isolated port 8784 review environment. Complete
the M8 visual matrix across all ten dashboard surfaces, Graphite/Ink/Paper,
1440/1280/1024/768/390 px, expanded/empty/error/stale states and 200% text.
Then verify the integrated Agent, exact human draft-review boundary, OpenAI
subscription route/model/reasoning persistence, bookmark portability/restoration,
GIF/MP4 decode/download and preparation-audit identity/overlay guards. Preserve
the user's separate port 8783 dashboard and all credentials.

Never allow the Agent to run simulations or autonomously apply scientific
settings. Do not change scientific algorithms, force fields, protonation,
defaults, seeds, atom ordering, units or analysis definitions. Do not copy or
disclose credentials. The user has OpenAI subscription access only; retain Claude,
Kimi Code and Gemini adapters but label real-account testing unverified. Preserve
Codex model order Astra, Sol 6.1, Sol 6, Luna 6, then the rest, and their supported
reasoning controls. Do not repeatedly retest accepted models without a route
change or new failure.

Keep scientific preservation claims tied to the exact tested fixture, backend
and platform; retain known skips, the baseline CPU repeatability failure and
provenance coverage limits without changing tolerances. Freeze the final code,
run the required combined checks without concurrent edits, build/install that
exact revision outside the checkout, and record the wheel hash and exact results.
Update the plan/status/changelog, commit and push reviewable changes only to the
user's branch. Report local/OpenAI acceptance separately from whole-framework
acceptance. Do not create or merge a PR unless asked.
