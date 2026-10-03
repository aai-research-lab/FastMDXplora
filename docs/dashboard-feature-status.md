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

### Theme contrast and editable-field boundaries

Browser-computed palette checks pass in Graphite, Ink and Paper: primary,
secondary and muted text plus cyan/orange/red/green status colors each meet
4.5:1 against primary, secondary and elevated surfaces. Primary-action text
meets 4.5:1 at both gradient endpoint colors. These are token/surface checks;
they do not certify every translucent composition, chart annotation or custom
component state.

Editable inputs, textareas and selects now use a separate visible control-border
token rather than decorative panel borders. Placeholder colors follow the muted
text token with full opacity, and keyboard focus retains an explicit outline.
Computed borders of Agent provider/model/key/budget controls pass 3:1 against
all three main surfaces in every theme. Source reports, scientific labels,
units, data, configuration defaults and provider routes are unchanged.

Contrast increment regression: all 69 Agent-boundary, clip and draft-review
checks passed, including the new three-theme palette/boundary checks. Six
report-rendering/dashboard checks also passed. Full rendered-state contrast and
final whole-framework acceptance remain open; these receipts do not replace them.

Bookmark capture scope is now visible before saving. It identifies the current
page/graph/range/setting/residue/frame, camera, pinned comparison, figure, warning
and preparation event where present. Editing without a new screenshot explicitly
retains the saved view; selecting a screenshot explicitly switches to the current
research view. Clear/save/study changes update the summary. All 49 research-view
and bundle checks passed. The browser flow checks scope transitions, restart,
search/filter, graph-range restoration, JSON/bundle export and import; its final
rerun also passed after figure/warning and study-change summary refinements.

### Latest installed-package receipt

Feature revision `f55ffa05fb6eaa74b7a561564eba3c1eeb7f3cc5` built as
`fastmdxplora-2.5.9.dev171+gf55ffa05f-py3-none-any.whl`, SHA-256
`6f345839bca2a6a66aff80be63140e8910af06377a628bc450bc32752902ff14`.
Installed without dependencies into a fresh temporary target, imported with
isolated Python outside the checkout, and served from that installed package.
Knowledge Markdown and runtime error registry match; the Gemini bridge resource
is present. Latest HTML/static markers for modal settings, capture scope, draft
review and field contrast passed. New preparation observer and draft-review
modules import from the installed package.

A headless browser against the installed server at 390 px passed settings modal
opening, Shift+Tab containment, Escape close/focus return and bookmark scope/close,
with no JavaScript errors. All 19 provider/reasoning regression checks passed in
the source checkout. No live provider inference or scientific preparation was
performed for this packaging check. Existing venv dependencies supplied the
runtime; clean-machine extras installation, complete integrated study workflow
and unavailable live-provider/scientific gates remain open.

### Preparation audit restoration and enlarged-text verification

All 10 preparation audit checks pass, including browser restoration of the
stage pair, aligned overlay, linked-camera setting and both camera views in
Graphite, Ink and Paper. Expanded audit controls stay within a 390 px viewport
at 200% root text size. Stage selectors now wrap within their labels on narrow
screens. Browser checks report no JavaScript errors and verify unchanged source
structure bytes. These synthetic UI checks do not establish additional chemical
validation or native browser zoom behavior. OpenAI is the user's only available
subscription for live testing; other provider subscription flows remain unverified.

### Browser GIF and MP4 export acceptance

The 23-check clip suite passes. The browser export workflow now covers GIF and
Both, using actual labeled, rotating saved frames at 640 by 480. The Both case
downloads MP4 and decodes its first frame with ffmpeg at the selected dimensions;
GIF contains three frames and provenance contains three source-frame/time entries.
Two targeted browser cases also pass after adding byte-for-byte comparisons
between downloads and saved artifacts, plus unchanged SHA-256 checks across setup,
simulation and analysis fixture files. Preview/export restore structure mode and
release viewer interaction; browser errors remain absent. This closes a browser
Both-format gap but does not replace the complete integrated-study gate or prove
every label's visible placement at every export resolution.

### Combined workflow with a real dashboard restart

`tests/test_research_workflow.py` passes one browser workflow in a single synthetic
completed study: select an RMSD range, save/export a bookmark bundle, stop and
restart the dashboard server, find/restore the persisted range, import the bundle,
inspect server-resolved Agent context, render/download GIF and MP4, and restore
a preparation event after changing its selection. All original fixture files
retain their SHA-256 hashes; there are no browser page errors. This checks combined
feature state beyond isolated tests. It uses a test fixture, not the user's
completed study; it does not call a provider, exercise graph-residue comparison,
or replace the remaining complete M7 live workflow and scientific gates.

The combined workflow now also passes keyboard selection of two chain-mapped
RMSF graph residues, pin/compare, inspection of the pinned residue's server
evidence, and a saved viewer bookmark round trip. After changing the frame and
clearing the comparison, Restore recovers frame 3, residue 2, pinned residue 1
and the exact saved camera before Both-format export. Original fixture hashes
and browser-error checks still pass. No inference is sent by selection/compare;
live explanation and the user's actual-study workflow remain separate gates.

### Current live OpenAI access and expired-receipt selector

After restarting the current branch dashboard on the completed 1L2Y study,
normal dashboard model checks succeeded through the connected ChatGPT subscription
for GPT-6.1 Sol, GPT-6 Sol and GPT-6 Luna. Settings showed Astra, Sol 6.1, Sol 6,
Luna 6, then the remaining catalog models, with the verified Luna model selected
and its reasoning levels visible. No API-key fallback was used. These three
tool-free access probes verify inference access, not a complete new study-context
explanation or account-expiry lifecycle test.

A visible discrepancy was fixed: when a saved model's one-hour access receipt
expires and the catalog omits it, Settings keeps that saved model selected and
labels it "not listed; check access" rather than displaying the first available
model. Applying it stays disabled until access is verified. The 19 provider and
reasoning checks pass, including the browser regression for this condition.

### Fresh live selected-graph explanation on the completed study

On the current dashboard serving the actual completed 1L2Y study, the connected
GPT-6 Luna subscription answered a new three-sentence question about selected
radius of gyration. It reported 0.7043 nm, uncertainty not determined, and separated
the report's passed equilibration check from its failed correlation-time check.
The UI attached the Rg evidence chip. Inspect current evidence resolved page
`analysis`, metric `rg`, figure `analysis/rg/rg.png`, mean 0.7043278748801384 nm,
null error, and the recorded insufficient-correlation-time explanation. The
study's analysis options and report evidence agree with those statements.

SHA-256 comparisons across 306 existing setup, simulation, analysis and report
files found zero changes after the explanation. No scientific action was requested
or initiated. This is a fresh actual-study live explanation receipt, not proof of
all context types, provider lifecycle states or the complete M7 walkthrough.

### Actual-study screenshot bookmark and stale loading notice

The completed 1L2Y study now contains a saved Graph-tagged Rg bookmark with title,
note, source checksums and an 844 by 689 screenshot. The saved screenshot was
visually inspected: it shows the graph, axes, legend and uncertainty notice, with
no chat or authentication content. Reloading the dashboard and restoring the
persisted record returned the selected Rg figure and reported "Bookmark restored."
The UI reported "Bookmark export ready", but the in-app browser download event
timed out and no local bundle path was verified; the actual-study bundle download
and import remain open, separately from passing fixture tests.

The walkthrough exposed a stale "Load a study" notice beside loaded bookmarks.
Rendering loaded records now clears that obsolete notice while preserving other
status/error messages. The browser save/reload/restore/export/import regression
passes with an assertion that the obsolete notice is absent after loading.
