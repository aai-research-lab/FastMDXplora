# Dashboard feature changes and milestone status

Reviewed on 2026-10-02 against the [full implementation framework](dashboard-feature-plan.md),
with implementation and verification revisions identified in the receipts below.
This is the current status; the framework's milestone receipts are historical.

**The complete framework is not finished.** All five feature areas have working
implementations, but several specified controls and release acceptance gates
remain open. A milestone commit records a tested increment, not completion of
every requirement in the expanded framework.

The [completion and aesthetics plan](dashboard-completion-plan.md) now defines
how to close these gaps. M8 has an implemented visual foundation; the full
page/theme/accessibility review remains pending. The implementation receipts
below distinguish completed increments from outstanding acceptance gates.

Work stays in the GitHub Desktop checkout on `princeote/context-aware-agent`,
based on the existing v2.5.8 branch history. Upstream main was not integrated.
These changes are not a published upstream release.

## Changes available on this branch

- **Context-aware Agent:** a persistent, disableable sidebar uses the current
  page, graph/range, warning, residue/atom, saved trajectory frame, preparation
  event and selected setting. The server resolves evidence from the current
  study. Packaged Markdown explains the workflow, units, interpretation limits
  and all 127 registered errors. Unknown external failures remain uncertain.
  Suggestions can be added to the human-reviewed builder draft; the dashboard
  Agent cannot execute a simulation or autonomously apply scientific settings.
- **Residue explanations and comparisons:** canvas and verified per-residue
  graph selections share protein identity handling. Pin one residue, select
  another and prepare an unsent comparison question. Measurements require
  verified analysis granularity and unambiguous topology correspondence.
  Cropped graphs retain their identities; stale or unmatched selections clear
  research selection instead of explaining the wrong residue.
- **Research bookmarks:** titles, notes, preset/custom tags, search/filter,
  optional screenshots and saved graph/viewer/settings/audit context. Study-local
  persistence supports JSON and portable screenshot bundles, import previews,
  collision choices, edit conflicts and source-fingerprint checks. Stale views
  keep notes/screenshots and explain why restoration is unavailable.
- **Trajectory clips:** export GIF, MP4 or both from saved viewer frames, with
  range/stride, playback fps, signed camera rotation and residue/atom/frame/time
  checkboxes. Source mapping, physical time, view, labels and encoder metadata
  accompany saved media under `exports/clips/`. Export cancellation/failure
  restores viewer state; rendering does not rewrite molecular coordinates.
- **Preparation provenance:** future preparations record observed choices and
  operations, source references, checksums and bounded snapshots separately from
  scientific outputs. Historical studies show available evidence and missing
  records without inventing the reason for a structural difference.
- **Preparation audit:** a collapsed panel offers saved-stage inventories,
  event evidence, linked side-by-side structures and an optional display-only
  overlay requiring exact heavy-atom correspondence. Selected evidence can be
  bookmarked or sent to the Agent. The factual view works without AI.
- **Subscription connections:** one OAuth provider dropdown and contextual
  Connect action for OpenAI/ChatGPT/Codex, Claude, Kimi Code and Gemini. Adapters
  restrict inference tools and isolate credentials from study exports. Copilot
  was removed from application scope. Other providers use their documented
  official-client bridges rather than a claim of universal direct OAuth.
- **Models and reasoning:** Codex ordering starts Astra, Sol 6.1, Sol 6, Luna 6,
  then the remaining models. Explicit access checks expose the three requested
  GPT-6 models when account inference succeeds despite catalog omission. A
  model-specific reasoning slider uses supported levels, preserves account/model
  preferences and disables unsupported controls. See the
  [model/reasoning contract](agent-model-reasoning.md). Dropdowns/options use
  black backgrounds and white text across dashboard themes.

## Requirement-to-milestone review

| Milestone | Current state | Evidence and remaining work |
| --- | --- | --- |
| M0 — reconcile | Implemented | Architecture/boundaries and branch scope documented; this review reconciles subsequent increments with the original full contract. |
| M1 — context and human control | Implemented; representative acceptance verified | Eight context types, installed knowledge, refusal boundary, server-resolved context inspection, per-message view opt-out and outbound evidence receipts. Before/after draft review is bound to candidate, builder baseline and study; final Agent-authored builder runs require fresh exact-state human review. Expiry, changed-state rejection, cancellation and intercepted browser-run checks pass; full integrated regression remains M7. |
| M2 — bookmarks | Implemented; representative acceptance verified | Persistence, tags/search, screenshots, portability, view restore and stale/import guards tested. Include it in the final broad browser/release pass. |
| M3 — providers | Implemented; live verification partial | OpenAI browser consent and real explanation inference succeeded, including the requested GPT-6 models. Claude, Kimi and Gemini have restricted adapters and synthetic/native transport tests, but their real account login, entitlement, model enumeration, inference and expiry/disconnect acceptance remain pending. |
| M4 — clips | Implemented; representative acceptance verified | Resolution presets re-render the molecular view at selected dimensions; study title/custom caption join residue/atom/frame/time overlays. First/last previews share the export renderer and show source mapping, physical time, playback duration and approximate upload size. Server dimensions/text bounds, decoded media, cancellation and restoration checks pass. Final integration remains M7. |
| M5 — provenance | Implemented; preservation verification limited | Recording and missing/limited evidence handling tested. Seeded audit-on/off equivalence passed on OpenMM Reference. Aggregate system snapshots do not record every individual solvent/ion operation. Broader backend/platform and fixture preservation checks remain open. |
| M6 — audit visuals | Implemented; representative acceptance verified | Stage strip, separate protein/recorded-ligand/water/ion/other bars, affected-residue category track, decision table, side-by-side/overlay and Agent/bookmark links. Recorded stages and observed identity differences are distinct; ambiguous mappings are unselectable and raw evidence remains expandable. Eight final audit checks pass; broader integration remains M7. |
| M8 — aesthetics and interactions | Foundation and text scaling implemented; full review pending | Toolbar/composer separation, consistent Agent typography, drawer focus return and relative text sizing. All ten sections pass wide/narrow shell checks and 200% root-text control-bounds checks in all three themes. Full expanded-state visual, contrast and keyboard/focus acceptance remains open. |
| M7 — integrated release validation | Partial; not complete | Targeted integrated suites, browser checks and earlier wheel receipts exist. Final expanded-scope acceptance, latest-wheel verification, remaining provider tests and broader scientific/browser coverage are not all complete. |

## Verification and scientific limits

Fresh verification during this status review: **65 passed in 113.23 seconds**
across `test_agent_reasoning.py`, `test_provider_connections.py`,
`test_research_bundle.py`, `test_clip_exports.py`, `test_preparation_audit.py`
and `test_preparation_recording.py`. This focused run verifies the existing
increments; it is not the outstanding complete M7 release pass.

The framework retains the original incremental receipts, including an integrated
236-pass/one-skip run at `88d63d5` and earlier package-resource checks. Those
receipts predate later model/reasoning and graph changes and must not be treated
as a fresh whole-branch release pass.

Subsequent receipts include provider/model reasoning tests and real OpenAI
inference. At `4ff7565`, all 23 Agent boundary checks and all 35 analysis graph
checks passed across the focused runs. Live completed-study acceptance selected
ASN A1 and LEU A2 from the RMSF graph and compared their saved C-alpha RMSF values
through OpenAI without running a simulation. The reply distinguished measured
values from unproved chemical explanations.

Scientific source checksums were preserved in display/export fixtures. The
preparation recorder's audit-on/off check produced identical PDB/System XML and
positions/box within `1e-10` on the explicit OpenMM Reference backend. This does
not certify all GPU platforms, membrane/ligand preparations or every scientific
output. These additions preserve the existing algorithms by design; broader
scientific equivalence still requires the framework's remaining checks.

## Remaining exit checklist

- Include the completed M1 context/draft-review flow in final integrated acceptance.
- Include the implemented M4 export controls/preview and M6
  residue/decision/component visuals in final integrated acceptance.
- Verify OpenAI with the user's available subscription. The user confirmed they
  have no Claude, Kimi or Gemini subscriptions: keep those providers' live model,
  explanation, cancellation, expiry and disconnect gates explicitly unverified.
  Their adapters and synthetic tests do not establish subscription availability.
- Run the final integrated suite against the completed scope, document every
  skip/failure, and rebuild/check the latest wheel rather than reusing an older
  wheel's validation receipt.
- Broaden scientific preservation checks on supported backends/fixtures and
  finish narrow-screen, keyboard and full interaction review. Do not launch a
  production MD study merely to test a dashboard control.
- Review the final branch diff and update this checklist before describing the
  whole framework as complete or ready for upstream acceptance.

This status update documents existing work and outstanding requirements; it does
not silently remove requirements from the approved framework.

## Completion implementation receipts

The context inspector/view opt-out and initial typography/toolbar changes passed
36 Agent boundary/streaming checks, including 390 px browser layout. Draft review
adds read-only differences with schema explanations, explicit human confirmation,
process-local signed review binding and expiry. Agent authorship is preserved
when loading the builder so final runs require exact-state review. Eight review
tests and the browser approval/cancel/intercepted-run flow pass; 13 focused
review/load checks passed after the final changes. No production simulation was
started for these checks. Scientific source files are not written by review.

A broader Agent-panel run exposed an API fallback-model list assertion that
conflicted with the earlier GPT-6 list change. The API-key fallback is now separate
from verified subscription availability. All 193 Agent-panel/reasoning/connection
checks passed after reconciliation; requested subscription models are retained.

Clip completion: all 16 clip checks passed, including a real browser preview,
selected-resolution GIF export with title/caption metadata, valid MP4/GIF decoding,
invalid dimensions/caption refusal, source preservation and cancellation/restore.

Audit visual completion: 56 audit/bookmark checks passed before the final ligand
classification refinement; all eight final audit checks and the five preparation
recorder checks pass. Ligand inventory requires saved parameterization names;
unknown components remain other. Recorded-stage snapshot differences remain
observations, not automatically recorded chemical causes. The user has only an
OpenAI subscription, so live Claude/Kimi/Gemini verification is an explicit
external dependency while their restricted adapters remain available.

Current integrated receipt: 126 checks passed across dashboard coverage, Agent
boundaries, exact draft review, MP4/GIF exports, preparation audit/recording,
research views and bookmark bundles. This includes seeded Reference preparation
equivalence; it does not establish equivalence on other platforms or fixtures.
Three additional browser checks passed for Graphite, Ink and Paper across 1440,
768 and 390 px on Overview, Analysis, Agent, Run and Settings. Bookmark panels
fit each width and close with keyboard activation. This is shell/layout evidence,
not a completed visual or accessibility review of every expanded feature state.

Installed-wheel receipt for feature revision `f42f7e7`: isolated build produced
`fastmdxplora-2.5.9.dev165+gf42f7e745-py3-none-any.whl`, SHA-256
`b205e38ba215dea68e7561a3dc7d1a8b39c1f6a279c61fd90f24e5cbc9c773c0`.
The development version comes from Git history; this is not a published release
or an upstream-main integration. Installed without dependencies into a separate
temporary target and imported with isolated Python outside the checkout. The
installed Markdown knowledge, exact runtime error registry, HTML, draft-review,
research and CSS assets passed resource checks. A test server using that installed
package served the dashboard and all three checked static assets successfully.
Existing venv dependencies supplied the runtime; this is not a clean-machine
dependency installation or final whole-framework packaging acceptance.

### Preparation operation coverage and current preservation limits

| Existing operation | Recorded evidence | Remaining limit |
| --- | --- | --- |
| Source/model/assembly selection | Supplied input snapshot, resolved input, selected model/chains and requested choices | Historical runs retain only their saved artifacts |
| Mutation/repair/protonation | PDBFixer stage snapshots, scheduled missing residues/atoms, substitution requests and explicit state choices | Hydrogen counts alone do not establish a chemical state; missing causes stay unspecified |
| Protein/ligand assembly | `assembled_solute` snapshot; ligand names and force-field files after the existing assembly succeeds | Pose/charge correctness is not certified |
| Membrane placement | `membrane_placement` snapshot with existing placement record and requested orientation/frame | Observation does not validate that the chosen membrane frame is biologically correct |
| Solvent/ion/bilayer construction | `solvent_ions` or `membrane_solvent_ions` snapshot after successful combined construction; requested choices and resolved padding/membrane record | OpenMM exposes a combined operation, not individual insertion causes or intermediate retries |
| System parameterization | `system_parameterization` event with actual particles/constraints/forces and resolved construction parameters | Successful parameterization is not a chemistry certificate |
| Written final system | `prepared_system` snapshot plus saved manifest decisions | Final snapshot is an aggregate result, not proof of all individual causes |

The extended recorder/audit suite passed 15 checks, including seeded Reference
audit-on/off equivalence and disabled/failing observer guards. A separate real
OpenMM POPC/water patch check passed on 32,512 atoms: observation preserved atom
order/identity, bonds, exact positions/box, System XML, source bytes and Python
random state. The snapshot was 3,009,688 bytes and took 0.959 s on this host. This
measures one observation, not full-pipeline overhead or a membrane-building run.
The existing membrane/ligand logic suite passed; real OpenFF ligand preparation
is unavailable here because `openff` is not installed. `openmmforcefields` alone
does not satisfy that dependency.

Two existing CPU repeatability checks failed on small box/coordinate differences
with recording enabled, and both also failed with recording completely disabled.
Do not claim deterministic CPU preparation or alter numerical/scientific behavior
to make this feature suite green. Reference equivalence remains the demonstrated
full-preparation preservation scope; fresh bilayer construction and real OpenFF
ligand preparation remain open scientific acceptance gates.

All 56 Agent-boundary/research-view checks passed after the drawer focus fix and
expanded layout coverage. The three theme cases now cover all ten sections at
1440, 1280, 1024, 768 and 390 px. Bookmark Close and Escape return focus to their
opening control; native dialogs retain their own Escape handling. Text zoom,
complete expanded-state visual inspection and contrast acceptance remain open.

Preparation compatibility receipt: 180 checks passed and two intentionally skipped
across setup phase/layer, guardrails, membrane placement/measurements and supplied
ligand/coordinated-ion handling. The skips are absence-of-dependency tests
(`run only when deps absent`, `graceful-degradation path only`); OpenMM/PDBFixer
are installed. This does not remove the separately observed CPU repeatability
failures or replace real OpenFF ligand/full membrane-build equivalence.

Live export-dialog inspection exposed native gray caption/title fields; these now
share the dialog typography/surfaces. All three new theme cases passed text and
placeholder contrast of at least 4.5:1 and field bounds at 1280 and 390 px. This
checks those fields, not every dashboard element. The local completed-study GUI
was restarted to serve the latest template, draft review and export options;
bookmark opening/closing and focus return were also verified in the live preview.

### Relative text sizing and scaled-control acceptance

Dashboard text tokens now use `rem` and the HTML root respects the browser's
default text size. Context evidence and draft-review cells use the same tokens.
The shared report stylesheet was not changed. A stronger browser check exposed
off-screen Overview chart reset, viewer center/representation controls and Agent
settings at 200% root text on a phone-width viewport. Card actions now wrap,
viewer labels stack on narrow screens, and Agent footer actions wrap.

All three theme cases passed across ten sections at 1280 and 390 px with the root
text size explicitly doubled. The test verifies that toolbar text actually
doubles, visible ordinary controls remain within viewport bounds, and bookmarks
remain operable with focus return. Hidden collapsed panels and locally scrolling
tables are excluded from the ordinary-control bounds check. This is browser
layout evidence for enlarged UI text, not a completed native browser zoom,
expanded-dialog or scientific figure visual acceptance gate.

Current typography regression receipt: 67 Agent-boundary, clip, draft-review and
drawing-script checks passed. Three additional cases passed for the expanded
clip dialog at doubled root text in Graphite, Ink and Paper at 390 px: visible
buttons, inputs, selects and label groups stay within viewport bounds and Close
remains operable. These checks do not certify every other modal, native browser
zoom behavior or whole-dashboard contrast.

Agent settings now uses a native modal dialog. It opens with Close focused,
cycles Tab/Shift+Tab through enabled visible controls, closes with Escape and
returns focus through native dialog behavior. Provider polling cleanup listens
to the dialog close event, covering both Close and Escape. Three browser cases
passed these interactions and visible-control bounds at 390 px with doubled
root text in Graphite, Ink and Paper. This replaces the earlier overlay's
unimplemented modal semantics; provider authentication and scientific execution
routes are unchanged.

Dialog regression receipt: the combined run passed all 36 current Agent-boundary
checks and 173 of 174 Agent-panel checks; the sole failure expected the removed
overlay markup. After updating that assertion to native closed-dialog semantics,
all 174 Agent-panel checks passed. Three focused theme/keyboard cases also passed.
