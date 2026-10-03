# Dashboard feature changes and milestone status

Reviewed on 2026-10-03 against the [full implementation framework](dashboard-feature-plan.md),
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
  then the remaining models. Published choices remain available despite catalog
  omission; an unchecked choice requires successful inference before applying. A
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
| M5 — provenance | Implemented; controlled POPC and OpenFF preparation verified | Recording and missing/limited evidence handling tested. Seeded audit-on/off equivalence passed on Reference, full POPC packing/relaxation under deterministic CPU test controls, and an installed-wheel peptide/ethanol OpenFF preparation. Aggregate system snapshots do not record every individual solvent/ion operation. Broader backend/platform/fixture checks and CPU repeatability baseline remain open. |
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

### Earlier installed-package receipt

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

### Actual-study portable bundle download and import verified

The in-app browser successfully downloaded `research-bookmarks.zip` through a
visible native attachment link. ZIP inspection found `bookmarks.json` and the
saved screenshot, with no CRC errors. Normal file-chooser import preview reported
one matching ID and one compatible view; Keep both imported one record. The
study store contains two distinct IDs, both retaining screenshots. All 306
existing scientific/report file hashes remain unchanged.

Exports now retain a visible native download link instead of a detached blob
link, with a manual retry instruction when automatic downloading does not start.
The link is hidden when records/filters rerender so an obsolete selection cannot
remain advertised. The 49-check research suite initially passed 48 checks and
exposed the import-link invalidation gap; after fixing that gap, the affected
browser save/reload/restore/export/import check passes again. Actual-study bundle
download/import is now verified; remaining walkthrough steps are still open.

### Actual-study clip export and discovered time-label discrepancy

The completed 1L2Y study exported a three-frame GIF and MP4 at 640 by 480 with
60-degree camera rotation, protein residue labels, source-frame labels, title
and caption. Preview completed, GIF decoded with three frames, and ffmpeg decoded
MP4 without errors. Downloaded MP4 matches the saved artifact's SHA-256. Closing
the dialog returns to the visible molecular viewer. The sidecar records source
frames 0, 30, 60, selected captions and label options. All 306 existing scientific
and report files retain their hashes.

Physical-time acceptance remains open: the existing playback reports 0, 0.03,
0.06 ns for those samples and a full range approaching 2 ns, while the recorded
production duration is 1 ns and resolved sampling is 250 steps at 2 fs. Playback
currently trusts MDTraj time values. The discrepancy requires source/provenance
reconciliation; media decoding does not prove these time labels correct. No
scientific data or settings were changed to conceal it.

### Playback timing correction from recorded sampling

The discrepancy is traced to MDTraj's DCD `read_as_traj`, which constructs its
time array from frame ordinals. Playback no longer treats those as picoseconds or
interpolates timestamps from a total duration. For a non-continuation fixed-step
production with recorded resolved interval, timestep and agreeing production/frame
counts, it maps sample index k to (k+1)*interval*timestep in nanoseconds, matching
the production reporter's first sample after one interval. Other cases retain
unknown physical time. This includes joined/continued studies until their segment
mapping is verified; live-history timestamps remain separately recorded evidence.
The playback cache signature includes timing-record identity and a timing-version
marker so old inferred labels cannot remain current.

Read-only evaluation against 1L2Y records maps source frames 0, 30, 60 and 1999 to
0.0005, 0.0155, 0.0305 and 1.0 ns. Fourteen timing/dashboard checks pass; the first
broader run passed all 23 clip checks and failed one old interpolation expectation,
which was corrected to require unknown time without sampling evidence. Lint passes.
The live preview and re-export with corrected labels still need verification;
existing clips and scientific artifacts have not been rewritten.

### Live timing verification exposed and corrected a camera-origin failure

Regenerated 1L2Y playback now starts at 0.0005 ns and ends at 1.0 ns; fresh clip
metadata for source frames 0, 30 and 60 contains 0.0005, 0.0155 and 0.0305 ns.
Only the derived `simulation/playback_index.json` changed among the 306 earlier
hashed files; playback coordinates and scientific inputs/results are unchanged.

Visual inspection initially found blank molecular images despite successful
encoding: preserving the static/live camera position when switching to saved
coordinates can point away from the molecule because their origins differ.
Clip capture now adjusts camera translation by the protein-center displacement,
preserving relative pan, orientation and zoom, without modifying atom coordinates.
The final actual-study GIF visibly contains the molecular cartoon and source/time
caption. Two browser export regressions pass with a new assertion for colored
molecular pixels above the captions. An attempted same-task redraw did not solve
the actual failure and was removed. Earlier blank exports remain historical
artifacts and are not accepted as visual passes. Final labeled rotating Both
acceptance after this camera fix remains to be checked on the actual study.
## Current-revision rotating media acceptance

On 2026-10-02, the dashboard was reloaded with revision `5f613f5` and the
completed 1L2Y study exported browser frames 0, 3 and 6 in both GIF and MP4.
The export used 640 × 480 pixels, 60 degrees of camera rotation, protein residue
labels and a study-title overlay. The saved metadata identifies source frames
0, 90 and 181 and physical times 0.0005, 0.0455 and 0.091 ns.

Artifact `exports/clips/c93390a953db4de28f5c616e57b04895` contains a 70,109-byte
GIF, a 21,809-byte MP4 and `view.json`. Decoding verified three GIF frames and
640 × 480 GIF/MP4 dimensions. The first and last GIF frames were visually
inspected: the molecular cartoon, residue labels, title and frame/time captions
are visible, and the final view has the requested rotation. This supersedes the
earlier empty-scene media acceptance failure; it does not certify full-solvent
motion or change the scientific trajectory.

The current code passed all 25 tests in `test_clip_exports.py`,
`test_playback_physical_time.py` and `test_research_workflow.py` in 121.83 seconds.
Two Pillow deprecation warnings remain. Provider scope is unchanged: the user
has OpenAI only; other providers require independently available accounts for
live verification and remain explicitly unverified.

## Clean installed-package and regression acceptance, 2026-10-03

Revision `6a138fc` built with isolated build dependencies as
`fastmdxplora-2.5.9.dev186+g6a138fca5-py3-none-any.whl`, SHA-256
`fbf09280a07ec66d54c4c484ca8c8f5751c85409178b752e7e4f04a11ab32dcf`.
The wheel and its `[agent]` extra were installed with normal dependency
resolution into a new Python 3.11 virtual environment, without system packages.
After installation completed, `pip check` reported no broken requirements.
Isolated imports outside the checkout verified the knowledge Markdown, exact
error-reference correspondence with all 127 runtime codes, Gemini bridge,
preparation-audit module and research/clip/viewer/provider/theme assets.

The installed CLI served an empty-study dashboard on loopback port 8783. Browser
interaction verified settings opening, modal Shift+Tab containment and Escape,
the bookmark panel's empty state, and black/white bookmark dropdown options.
No JavaScript errors were captured. The temporary server was stopped after
testing. This is fresh-environment Windows core/Agent acceptance; it does not
certify chemistry extras, another OS, provider inference or every installed
completed-study workflow.

All 115 tests in dashboard coverage, Agent boundaries, research views/bundles,
preparation audit and reasoning passed in 187.52 seconds. The separately selected
12 theme/layout/text-zoom/contrast checks passed in 73.63 seconds.

A new browser regression uses static fixture coordinates translated 100 angstroms
relative to playback. Both preview frames contain molecular color above the
captions; original camera and structure mode return, and source hashes are
unchanged. It passed in 14.13 seconds. Its initial test assertion incorrectly
required an absent static-view `frame` key; the corrected assertion compares
optional frame values. No production code or scientific behavior changed in
this acceptance increment. Full membrane/OpenFF preservation, CPU baseline
failures, complete visual-state review and unavailable provider account gates
remain open.

## Full membrane preservation and normal-playback correction, 2026-10-03

A real full POPC preparation with recording disabled/enabled passed in 110.90
seconds. This exercises PDBFixer, hydrogen preparation, membrane placement,
actual OpenMM bilayer packing and relaxation, solvation/ionization and final
system serialization. Input/prepared/topology PDBs and system XML were identical;
positions and box vectors agreed at absolute tolerance `1e-10`. The enabled audit
was complete without warnings and included membrane placement, membrane
solvent/ions and system-parameterization events. The disabled run wrote no audit.

The comparison harness fixes the membrane relaxation integrator seed and uses
single-thread deterministic CPU forces; PDBFixer hydrogen minimization uses
Reference. These test-only controls isolate observation from independent
backend/stochastic differences without changing production defaults, steps or
algorithms. This verifies the full small-peptide/AMBER14/POPC case, not every
lipid, protein, backend or OpenFF ligand route. OpenFF and the separately failed
CPU repeatability baseline remain unresolved.

The previously installed wheel exported visible GIF/MP4 files from a copied
completed 1L2Y study, but normal frame stepping exposed a blank molecular scene
and a blank viewer-bookmark screenshot. That screenshot is a failed acceptance
artifact. The static/live camera origin was retained when loading the trajectory
with a different origin. The camera translation is now corrected centrally on
entry to playback, retaining protein-relative pan, orientation and zoom without
moving atoms or recentering each frame. Clip export uses the same transition
and no longer applies a second correction. Three clip/translated-origin browser
checks passed in 48.79 seconds, including normal frame stepping, visible scene,
camera restoration and unchanged scientific source hashes. A new installed-wheel
check is required after this production UI correction; the earlier wheel does
not prove that it is fixed in an installed distribution.

The broader real-browser viewer/integrated research workflow and JavaScript
parse suites passed all 28 tests in 104.04 seconds after the correction.

## Corrected installed completed-study acceptance

Feature revision `b103dd8` built as
`fastmdxplora-2.5.9.dev189+gb103dd8df-py3-none-any.whl`, SHA-256
`e76a8060157b5048dac322bba8249370783558dad8cdb4eb1edf8e89163861cf`.
The earlier wheel was replaced in the fresh isolated Windows environment; its
previously resolved core/Agent dependencies were retained. `pip check` passed.
The CLI was restarted outside the checkout against a copy of the completed
1L2Y study. All 309 copied setup/simulation/analysis/report and root scientific
records retained their baseline hashes throughout the workflow.

Normal frame stepping now shows the molecular scene. A new frame-1 screenshot
bookmark contains the visible cartoon (1276 × 980 pixels), unlike the earlier
failed blank artifact. Changing frame and zoom, then restoring, returned frame 1.
Filtered bundle export downloaded through the native browser file link; its ZIP
passed CRC checking and contained one bookmark plus the exact screenshot. The
import preview reported one matching ID and one compatible view; Keep both
created a second unique record with the same screenshot. After a full installed
server restart, both records persisted and restoring the imported record showed
frame 1 and a visible scene. No JavaScript errors were captured.

Installed GIF/MP4 export `exports/clips/3fd7c0cdd46045f5b9083925d16eb5a0`
contains two visible 640 × 480 frames with 45-degree final camera rotation.
GIF/MP4 decoded successfully; metadata records source frames 0 and 60 and
physical times 0.0005 and 0.0305 ns. The earlier installed historical audit
interaction also restored the observed 3033-water-atom inventory event and
clearly identified missing causal provenance; it did not reconstruct a chemical
cause. This is completed-study viewer/bookmark/media/restart acceptance on Windows,
not an installed live-provider inference or chemistry-extra certification.

## Expanded audit and context layout acceptance, 2026-10-03

The preparation-audit browser regression now exercises all five planned widths
(1440, 1280, 1024, 768 and 390 pixels) at 100% and 200% text size in Graphite,
Ink and Paper. The comparison, aligned overlay, selected source/stages and raw
event disclosure remain expanded. All ordinary audit controls stay within the
viewport; both camera arrays, linked/overlay choices and selected stages remain
unchanged across resizing. The source structures retain identical bytes. All
three theme cases passed in 59.92 seconds.

The context-inspector/opt-out browser check now uses the same 30 theme/width/text
combinations. The expanded outbound evidence receipt and composer controls stay
visible and within the viewport; view-context opt-out remains off. Each case
also verifies that the outgoing request actually excludes selected-view context
and retains the last-message evidence receipt. All three cases passed in 9.78
seconds. Provider replies here are controlled test replies, not live inference.

These checks extend expanded-state acceptance for two specific surfaces. They
do not replace visual inspection of populated/error/loading/import/draft states
or native open dropdown menus, and M8 remains open for those remaining states.

### Installed-wheel OpenFF preservation receipt

The isolated Ubuntu/WSL Python 3.11.16 environment installed the corrected
`b103dd8` wheel outside the source tree, alongside OpenFF Toolkit 0.18.0,
AmberTools 26.0 and openmmforcefields 0.16.0. The new
`test_openff_ligand_preparation_preserves_outputs_with_audit_on_or_off` passed
in 26.42 seconds (one selected test). It runs actual peptide/ethanol preparation
and ligand parameterization with an available AM1-BCC provider, fixed ligand
file coordinates and the existing scientific clash check. PDBFixer uses the
same explicit Reference platform in both comparison runs; no production
scientific implementation or defaults were changed.

Audit on/off produced byte-identical supplied-input/prepared/topology PDBs and
System XML, unchanged supplied SDF bytes, and matching state coordinates and
periodic box vectors at absolute tolerance 1e-10. Recording was absent when
disabled and complete without recorder warnings when enabled. OpenFF emitted
upstream deprecation and preset-charge/virtual-site warnings; these are retained
as test diagnostics, not concealed or converted to recorder failures.

The passing comparison is scoped to this ligand/peptide and installed runtime.
It does not certify all ligand classes, charge/pose correctness, every platform,
or all chemistry extras. `pip check` in this conda environment reported missing
netCDF4/pdb2pqr/requests and NumPy/Biopython constraints in AmberTools auxiliary
packages (fetkutils, packmol-memgen, proprep, ndfes, edgembar). Those tools were
not called by the comparison; a whole-environment dependency PASS is not claimed.
The separately observed CPU repeatability failures and remaining M7/M8 acceptance
items remain open. The OpenFF package installation was isolated in the user's
WSL cache and did not modify Windows Python or the user's study.

### Repeatability attribution and draft-review layout acceptance

The installed `b103dd8` wheel passed both unchanged fixed-seed and drawn-seed
repeatability checks under Ubuntu/WSL Python 3.11.16 (2 passed in 22.88 s).
To investigate the earlier Windows failures without changing scientific code,
revision `51ac6d3c09ce7af54a5aa14a57833079bfacb28f` (the parent before preparation
provenance was introduced) was archived into an isolated temporary directory.
The baseline preparation module import was verified to resolve to that archive.
Using the existing Windows Python 3.11.9 scientific dependencies, its unchanged
repeatability checks gave 1 failed / 1 passed in 165.81 s. The fixed-seed test
failed at exact box equality: identical 4,457 atom counts but smallest box
widths 2.89883901163807 versus 2.898933121250925 nm. The drawn-seed test passed
on this run. This independently reproduces a preparation repeatability failure
without audit instrumentation. It does not identify the backend root cause or
establish deterministic Windows behavior. Scientific code and original numerical
assertions were left intact; the issue remains a documented baseline limitation.

The expanded human draft-review browser check passed for Graphite, Ink and Paper
(3 passed in 20.18 s). The populated field-difference dialog was checked at
1440/1280/1024/768/390 px with 100% and 200% text size: controls stayed within
the viewport, the pH difference remained present, approval stayed disabled and
no draft acceptance request was sent before confirmation. The existing test
then exercised explicit draft acceptance, final-run review cancellation and
confirmation, exact review-token validation, and persisted sidebar disablement.
The final run was intercepted by the test so no simulation started. Model
suggestions were controlled fixtures; this is not a live provider receipt.
Ruff (existing E501 exception) and `git diff --check` passed. M7/M8 remain open
for their remaining integrated and visual states.

### Populated bookmarks, missing images and import-preview layouts

The populated research panel and import-preview browser matrix passed under
Graphite, Ink and Paper at 1440/1280/1024/768/390 px and 100%/200% text size.
Each disposable study contained 18 records with long unbroken titles, detailed
notes and tags. One saved image was deliberately removed from that disposable
study: the server's actual missing-image message appeared in its card, the note
was retained, and no broken thumbnail remained. Other records had no screenshot.
Visible controls stayed inside the viewport and the bookmark card content did
not overflow horizontally. JSON export and file import reached a preview with
18 matching IDs; preview/layout interaction did not apply an import or change
the 18 persisted records. The RMSD source bytes stayed unchanged and no browser
page errors occurred. The three matrix cases plus the existing screenshot
persistence/metadata-normalization test passed (4 passed in 16.13 s).

This adds 60 populated-panel/import-state layout combinations, including the
missing-image fallback. It covers controlled fixture state, not a live provider
or scientific inference. Ruff with the existing E501 exception and diff whitespace
checks passed; two existing import-order issues were normalized. M8 still needs
its remaining loading/error/stale and visual review states, and M7's final
integrated acceptance remains open.

### Agent loading/failure layouts and cancellation regression

The Agent's pending-response and provider-failure states passed the browser
matrix in Graphite, Ink and Paper at 1440/1280/1024/768/390 px and 100%/200%
text size (3 cases passed in 13.92 s, 60 state/layout combinations). A controlled
completion was held while the actual dashboard request remained pending, then
released with a typed provider connection failure and a long readable message.
The button exposed Stop while pending and Send after failure. Visible controls
and transcript content stayed within the viewport, no run action appeared, and
the inspected setup/simulation/analysis fixture files remained byte-identical.
This uses an isolated synthetic completion, not an expired live subscription.
The first test run found an ambiguous test locator matching both the HTML root
and the Agent page; the locator was scoped to the page and rerun successfully.
No product-code correction was needed.

Three existing boundary regressions also passed in 9.04 s: the server refuses
a reply after its study changes without returning an answer; the real browser
shows incremental text and stopping restores Send without completing the old
reply; and a browser disconnect cancels the buffered client's completion.
The cancellation cases use a controlled local HTTP provider. These receipts
verify behavior and layout within their explicit scope, not real-account expiry,
account switching or other subscription providers. Ruff with the existing E501
exception and diff whitespace checks passed. Remaining integrated acceptance
and native visual inspection gates are still tracked in M7/M8.

### Native Paper review and bookmark field usability correction

Native review of the installed completed 1L2Y study at 1280 x 720 showed a
visible molecular cartoon, legible playback controls, readable RMSD axes/range
controls and contextual actions, and a coherent export dialog with its fixed
action area. Historical RMSF ambiguity was disclosed instead of mapped to a
false residue. The review found an actual bookmark-panel usability gap: title,
note and search fields retained narrow browser-default sizes within a wider
panel. Dashboard-only CSS now stretches these fields to the available width,
uses theme text/background/border tokens, enlarges the vertically resizable note
area and supplies clear keyboard focus and placeholder styles. No scientific
values, state serialization or report styles changed.

The populated/missing-image/import-preview, whole-dashboard bookmark shell and
200% text-size regression selection passed (9 passed in 71.19 s) across all
three themes. Native screenshots captured the pre-correction field issue;
post-correction installed rendering and the final wheel remain to be verified.
A `7e9f439` wheel built successfully using isolated build dependencies; an
initial no-build-isolation attempt failed because this venv lacked bdist_wheel.
That earlier wheel precedes this CSS correction and is not the final artifact.

Post-correction installed receipt: the `ba61c20` wheel
`fastmdxplora-2.5.9.dev196+gba61c2062-py3-none-any.whl` built with SHA256
`8b8505737a956fa0e346dea5762f901e1d4c1a9c2ca8a2225783d6aa982bc96b`.
It replaced the older package in the existing clean Windows acceptance venv
without adding source-tree paths. `pip check` returned no broken requirements.
The installed CLI was restarted outside the checkout against the disposable
completed-study copy. Native Graphite review at 1480 x 668 confirmed title,
note and search widths of 375.2 px, a 96 px note area, theme-aware rendering
and a visible focus ring; the earlier narrow-field issue is corrected.
All 309 baseline completed-study files still match their prior SHA256 hashes.
The review server/tab remain available for the next integrated/native checks;
they use the disposable copy, not the original user study. The pending/failure,
import and draft matrices provide automated evidence; the native review remains
partial for other themes/pages/states, and does not prove all dropdown popups.

### Published Codex choices and reasoning controls

The model selector now keeps Astra, Sol 6.1, Sol 6 and Luna 6 available even
when the subscription catalog omits them or a short access receipt expires.
Model names stay plain, ordered Astra → Sol 6.1 → Sol 6 → Luna 6 → the rest.
Verification status is shown separately in help/status text. Applying an
unchecked published choice performs a tool-free subscription probe before
changing the saved selection; a failed probe preserves the current model.
Arbitrary uncatalogued model IDs still fail. The access-check action now probes
all four models, including Astra. Keeping the rows also keeps documented
reasoning metadata available, fixing the disabled Luna slider after receipt
expiry. A changed slider remains pending until the user applies the model.

The updated provider/reasoning selection passed 19 tests in 14.05 s, including
failed-probe selection preservation, rechecking an expired choice, all four
model access probes and enabled keyboard-adjustable Luna reasoning in the real
browser. Existing import ordering was normalized; Ruff with E501 exception and
diff checks passed. OpenAI Astra/Sol 6.1 model documentation was rechecked on
2026-10-03; the supported effort values remain model-specific.

A consolidated run started at `35168e0` finished with 168 passed / 1 skipped in
656.56 s across research views/bundles, clips, audit/provenance, human-review and
Agent/provider controls. The skip was the missing Windows OpenFF backend, with
its installed Ubuntu comparison already verified separately. Model-control code
changed during this broader run, so that run is a baseline consolidated receipt;
the separate updated 19-test provider/reasoning run covers the later change.
Do not label the overlapping run an exact-final-revision full release PASS.

Live installed-dashboard access probes succeeded for Sol 6.1, Sol 6 and Luna 6.
Astra completed a medium-effort reply using the required SAY protocol; its first
plain connection-test reply failed config/protocol validation and remains an
explicit diagnostic. Sol 6.1 at high effort completed a normal RMSD explanation,
retaining recorded uncertainty and distinguishing 1 ns production from 2.5 ns
including equilibration. A contrived Sol 6.1 connection echo also failed protocol
validation; successful inference and normal explanation are distinct receipts.
These live results use the pre-change installed package; the revised dropdown's
installed/native acceptance remains pending its rebuilt wheel.

### Installed model-control acceptance at d0b2a3b

The rebuilt wheel `fastmdxplora-2.5.9.dev198+gd0b2a3bbb-py3-none-any.whl`
has SHA256 `fe7ecc8f320d08b403fab1602c1b643160b6c8bd7d56b659bac6da399ddacf63`.
It is installed in the isolated Windows acceptance venv; pip check reports no
broken requirements. Its CLI runs outside the checkout against the disposable
completed-study copy. Native dashboard inspection confirms the first four rows
are Astra, Sol 6.1, Sol 6 and Luna 6, enabled with plain labels. Each option's
computed background is black and text white. Sol 6.1's slider responds to Home
and arrow keys and displays high at the supported position. This verifies the
installed controls and computed styling; it does not certify every native OS
popup presentation. Luna with provider-default effort remains the saved choice.

The preceding live Sol 6 medium-effort test also completed a normal RMSD
explanation, retaining the recorded standard error, effective samples and failed
correlation-time qualification. Together with the preceding Astra and Sol 6.1
receipts, this demonstrates inference for the requested models at those tested
efforts, without claiming every effort or scientific question has been tested.
All 309 baseline completed-study files remain byte-identical by SHA256 after
the live explanations and installed model-control inspection. Full framework
completion and the remaining M7/M8 gates are still open.

User acceptance on 2026-10-03: the user confirmed, "sol works the slider works
aswell." This corroborates the Sol/reasoning interaction without identifying an
additional exact model version or effort. Native Paper review also confirmed
readable Report/Files layouts, a working saved HTML report preview and a visible
historical PDF dependency notice. A measured visual defect remains: at the
supported 180 px sidebar width, the brand text has about 100 px available while
its product-name line requires 174 px, clipping the name. This is a pending M8
correction; page-shell overflow checks alone did not detect it.

### Coherent model/research regression and sidebar correction

The combined nine-module run completed with 168 passed, 1 skipped and 5 Pillow
deprecation warnings in 615.65 s. It covers provider/reasoning, research
views/bundles, clips, audit/provenance, Agent boundaries and exact draft review.
Production/test code remained at d0b2a3b throughout; 3f1f648 only added docs.
The skip is Windows OpenFF unavailability, with separate installed Ubuntu
evidence above. This is a coherent combined feature receipt, not certification
of every M7/M8 requirement or all scientific/platform combinations.

The native sidebar clipping defect was reproduced by a new browser regression
in Graphite, Ink and Paper at the supported 180 px width (3 expected failures).
Dashboard-only product-name wrapping and a legible multiline line height fix
the overflow without reducing text size or removing the name. The new check
then passed at 180/232/320 px sidebar widths and 100%/200% root text size in
all three themes, including collapse/expand. Together with whole-dashboard
shell and doubled-text controls, 9 checks passed in 105.64 s. Ruff with the
existing E501 exception and diff checks passed. This CSS correction postdates
the combined run and installed wheel; its installed/native acceptance and the
remaining broader M7/M8 gates remain open.

### Installed study context and interface acceptance, 2026-10-03

A fresh Windows venv installed the 7405f99 wheel with declared core and Agent
dependencies, outside the source checkout. Pip check reports no broken
requirements. Packaged knowledge covers all 127 registered errors; the Gemini
bridge and dashboard research assets are present. Wheel SHA256:
`cdf0efcd80299e68468ba189d52c9adbc26f1b23b5d2839588e67eedbc807701`.
Its isolated dashboard on port 8784 uses a new completed 1L2Y study copy and
does not use the user's authenticated settings or conversation history.

Native interaction verifies a cropped RMSD range of 0.1–0.2 ns, server-resolved
context inspection, and explicit per-message view opt-out. Inspection retains
the study's full-analysis statistical qualifications and distinguishes selected
view hints from recorded evidence. No inference was sent in this isolated
environment. A titled Graph bookmark with a screenshot exports as a ZIP,
passes archive integrity checking, and restores the range after Reset. The
screenshot is 844 by 475 pixels. All 309 baseline study files remain identical
by SHA256. Restart/import and the remaining integrated acceptance are pending.

Further source-browser review corrected Settings' misleading API-only Engine
label, added keyboard controls and ARIA values to all layout splitters, preserved
a saved zero-height file pane, and made branding fit ordinary sidebar widths
while retaining wrapping for enlarged text. Seventeen targeted interface checks
pass across Graphite, Ink and Paper in 102.04 s. Cropped equilibration shading
now stays within the chart, with no excluded-region label after the cutoff.
All 38 analysis-chart checks pass in 45.63 s. An initial residue-selection check
clicked below the viewport; scrolling the chart into view corrected the test
harness while retaining its actual residue and viewer assertions. Scientific
values and source structures were not changed. Ruff and diff checks pass.
These additional changes postdate the 7405f99 wheel; their installed acceptance
requires the next build. M7/M8 and the full framework remain open.

### Connected subscription overridden by ambiguous Save, 2026-10-03

The user's live Agent showed a missing API-key error despite a connected
ChatGPT account. Settings showed Anthropic as the active engine while retaining
the ChatGPT account/model controls. Selecting "Use this account and model"
restored GPT-6 Luna with the user's displayed max reasoning choice. A native
message then completed successfully. The model appropriately did not claim
to independently verify authentication; the selected route is application state.
No new key or credential was entered or copied, and no simulation was run.

The generic global Save action previously selected the API route. It is now
explicitly labelled "Use API key or local server" and placed in that section,
with guidance to apply Codex choices using the subscription button. The error
message explains the selected API route and the subscription alternative.
Seven targeted checks pass in 19.69 s, including subscription apply with
reasoning in three themes, no API write on apply/Close, dialog keyboard layout
and missing-key guidance. Ruff passes for the updated boundary tests. Eight
pre-existing import-order/module-placement findings in test_the_agent_panel.py
were also reproduced from HEAD; no unrelated reformatting was made.
Installed acceptance of these new settings labels remains pending.

### Installed 1276612 acceptance and live route recovery

The installed wheel is `fastmdxplora-2.5.9.dev202+g1276612a4-py3-none-any.whl`,
SHA256 `a13c564d6af35055727a7730c34c3489b2c48d0ec47539951d8306250b5daee7`.
The clean acceptance venv passes pip check and verifies all 127 packaged error
references and required UI/bridge assets. Its server restarted outside the
checkout on port 8784. Native keyboard Home/End sets sidebar widths 180/320.
The saved bookmark survives restart; ZIP import previews one matching ID,
Keep both creates distinct bookmark IDs with the same verified screenshot,
and Restore returns the RMSD range to 0.1–0.2 ns. All 309 study baseline files
remain unchanged after this flow and the subsequent exports and explanations.

The installed viewer previews and exports two frames at 640 by 480 with all
six label toggles, protein label scope, a title/caption and 45-degree camera
rotation. Both GIF and MP4 artifacts are present under clip ID
`8b00b2f853064fa6aa1383019fc12fa8`; decoding verifies two frames, 10 fps and
0.20 s MP4 duration. Metadata records browser frames 0/6, source frames 0/181
and physical times 0.0005/0.091 ns. All-protein atom labels are visibly crowded;
selected-residue/atom scope remains the more readable option for dense scenes.
The export observation timed out, but the same tab subsequently reported
successful saving and both download links; no duplicate export was started.
Viewer frame 0 is restored. Historical audit inventory and a grouped HOH
difference prepare an unsent Agent question with missing-cause qualification.

The user subsequently connected accounts in the previously isolated 8784
settings. That instance also had API selection despite connected ChatGPT rows.
Applying its displayed GPT-5.6 Luna/medium choice restored subscription use;
the Agent completed an RMSD-minimum explanation retaining the failed
correlation-time qualification. Port 8783 separately retains GPT-6 Luna/max
after reload. Both show the explicit API/local-server action. Port 8783 loaded
updated UI assets while its Python process remained at the earlier revision;
port 8784 is a restarted process from this exact installed wheel. No credentials
were copied between these settings. Earlier missing-key messages remain in
saved history; the successful new replies are separate receipts.

The frozen 209-check combined feature run at 1276612 completed with 208 passed
and 1 failure in 459.82 s. The graph-residue comparison test timed out before
viewer-script initialization, then passed unchanged in isolation in 16.36 s.
The failed combined run remains a failed gate; it is not replaced by a claim
that isolated passing proves the whole release. A fresh combined run without
concurrent native rendering is underway. M7/M8 and broader acceptance remain open.
