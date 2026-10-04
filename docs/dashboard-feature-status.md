# Dashboard feature changes and milestone status

Reviewed 2026-10-03 against the [full implementation framework](dashboard-feature-plan.md). This is the current acceptance status; the lower receipts retain earlier evidence and limitations.

**The framework is not fully complete.** Core additions are implemented and the corrected package has passed the combined suite and representative installed workflows. Remaining work is requirement-by-requirement acceptance reconciliation, the single coherent M7 integrated workflow and bounded recorder overhead/storage acceptance. Claude/Kimi/Gemini live accounts and OpenFF/backend coverage remain explicit dependencies or qualified limits. Do not describe these as universally verified.

### Current checkpoint — 2026-10-03

The authoritative checkout is `C:\Users\User\OneDrive\Documents\GitHub\FastMDXplora`, branch `context-aware-agent`, tracking `princeote/context-aware-agent`. Current behavior revision is `a06a214`; subsequent revisions change documentation only. Local and remote heads were verified equal at `2f5ea32` before this reconciliation. The working tree was clean. Retain the v2.5.8-based history; upstream main has not been integrated and no PR created. The user's dashboard on port 8783 was untouched; corrected installed-package review is on port 8787.

The current wheel is `fastmdxplora-2.5.9.dev218+g6d5407a86-py3-none-any.whl`, SHA-256 `4250c2d6fc42abcd4eb451eb033b5912e99fb328f94daf05223bd6ebc9c30a74`. It is installed outside the checkout in a separate environment reusing repository dependencies. This is not clean-machine dependency installation or a published release.

The corrected-source ten-module combined suite passed **241 tests, 1 skipped**, in 765.66 seconds. OpenFF ligand preservation is skipped because `openff.toolkit` is unavailable. Five warnings concern Pillow clip-test assertions. CI Ruff F/B and diff checks pass. The disabled-Agent Send and docking fixes are included in this revision and suite.

The closeout diff since `b103dd8` is limited to GUI, tests and documentation. That comparison does not certify all inherited changes relative to the v2.5.8 tag. Scientific preservation checks remain fixture/backend-specific.

The receipts below retain their original revision and historical state. Statements such as “in progress”, “must rerun” or “native popup unverified” in an earlier receipt are superseded by the later corrected-source, OpenAI and Windows-menu receipts. Use this checkpoint and the operational checklist for current work.

### Exact-wheel integration receipts

- **Agent and OpenAI:** Applying the connected account/model selected GPT-6 Luna with max reasoning. A fresh question about the visible Preparation audit returned an evidence-bounded answer without an API-key prompt. The response distinguished an observed ASN A:1 hydrogen difference from a recorded operation, did not infer a chemical cause/protonation decision, and did not edit settings or run a simulation. Astra, Sol 6.1, Sol 6, Luna 6 ordering and 21 populated select lists/options with black/white computed styles were verified. The selected-account label and credentials were not recorded.
- **Bookmarks and audit:** The exact wheel exported JSON plus a screenshot ZIP, showed collision preview, accepted skip-existing and keep-both import choices, persisted through reload, and restored a saved view. The audit bookmark restored its Preparation tag, event, stage pair, display-only overlay and linked state. Two original study bookmarks remain. Changed/missing-source refusal is covered by source regressions; the missing-analysis-source test preserves its note/screenshot while refusing restore.
- **Trajectory clips:** Preview and download produced a two-frame 640×480 GIF and H.264 MP4; ffmpeg decoded both MP4 frames. Metadata downloaded. Selected-residue labels, atom/residue/frame/time/title/caption overlays, camera rotation and frame range were exercised. The viewer returned to the original frame, selection, camera and display; Follow, playback, live-update and spin controls were restored. SHA-256 checks for 315 non-output study files were identical before/after the test.
- **Preparation display:** Exact-wheel bookmark round-trip retained the selected saved event, before/after stages, overlay and linked view. Previous v7 study checks recorded a display-only heavy-atom overlay (154 exact matches) without changing scientific-file hashes. The overlay is visualization only, not trajectory RMSD or a preparation-quality score.
- **Aesthetics:** The source visual matrix reported zero geometry issues in 300 route/theme/viewport/text-scale states and 30 screenshots were inspected across Graphite, Ink and Paper. On the exact wheel, all 21 populated dropdown options compute black backgrounds with white text; closed controls look correct. The in-app capture exposes expanded control state but not the OS-native option popup, so its open rendering remains unverified.

### Additional exact-wheel acceptance — 2026-10-03

The installed wheel on port 8786 returned the draft-review difference for `setup.ph` (7.4 → 6.5), including schema help. It refused acceptance after a changed draft, changed builder baseline, absent human confirmation, or changed review purpose. This exercised the real HTTP endpoint, not an imported source function. No valid acceptance was submitted and no scientific setting was applied. SHA-256 inventories for all 315 non-output study files were identical before and after. Study-revision invalidation, browser review interaction and expiry remain separately covered by source tests; this receipt does not claim their installed end-to-end acceptance.

### Installed missing-source acceptance — 2026-10-03

The exact wheel HTTP flow saved and restored a temporary RMSD graph bookmark. With its RMSD source temporarily absent in the disposable study, restore refused with a source-difference error while retaining the note. Returning the original source restored successful acceptance. The QA bookmark was removed and all 315 non-output study hashes matched. This checks endpoint behavior, not browser refusal rendering or screenshot retention. A real server restart remains unverified: automatic tool policy rejected the proposed QA-process stop before execution; no server was stopped.

### Installed browser refusal and resource acceptance — 2026-10-03

The exact wheel browser check displayed the missing-RMSD-source refusal, retained the bookmark note and loaded PNG, and left the current page/frame unchanged. The original source was returned and the temporary bookmark deleted; all 315 non-output study hashes matched and no page JavaScript errors occurred. This closes the browser-rendering and image-retention gaps left by the preceding HTTP receipt. Separately, imports from the installed environment outside the checkout verified all 127 registered error IDs and disclosure levels in Agent knowledge, exact equality of bundled `errors.md` to the generated reference, and the external/unclassified disclosure. No source code changed.

### Reopened source acceptance — disabled Agent Send boundary

Installed-wheel interception reproduced a request from the Agent page Send button while the sidebar preference was disabled; selection-based Ask already refused. A source guard now refuses Send before modifying the composer/history or initiating inference. The three-theme regression passed 3/3, checking no propose request, preserved composer text and a visible refusal in the Agent transcript. CI Ruff F/B and diff checks pass. This code change reopens the combined suite and final-wheel build/integration gates: the earlier 02917ed wheel receipts remain historical evidence and do not verify this fix. The prior installed browser check also confirmed the review confirmation gate, controlled changed-baseline refusal before HTTP acceptance, cancellation, and sidebar-disable persistence on reload; actual study-switch interaction remains unverified.

### Agent docking selector correction — 2026-10-03

Installed-package testing reproduced a HierarchyRequestError when disabling the Agent on its own page. Navigation sets `data-page=agent` on the HTML root as well as the Agent section; the broad selector chose the root and attempted to move it after a descendant. Both docking selectors now explicitly target `section.page[data-page=agent]`. Regression coverage toggles the preference on that route, confirms the root marker, checks no browser error and a visible Agent section. The prior package is not the final candidate; this correction requires a new frozen build and combined acceptance.

### New wheel and process-restart acceptance — 2026-10-03

Candidate `6d5407a` built as `fastmdxplora-2.5.9.dev218+g6d5407a86-py3-none-any.whl`, SHA-256 `4250c2d6fc42abcd4eb451eb033b5912e99fb328f94daf05223bd6ebc9c30a74`. Installed outside the checkout in a separate environment reusing repository dependencies; this is not clean-machine installation evidence. Browser testing of this candidate passed visible disabled-Agent refusal, retained composer, zero inference requests, disable persistence after reload and zero page errors. The docking source regression passed 3/3 themes. Bookmark HTTP persistence also passed a genuine process restart: harness-owned Python processes 31348 and 28020 each started an installed-package server, exited normally, and loaded the identical saved record; graph range restoration passed and the temporary record was deleted. Existing dashboard servers were untouched. A preceding same-process server recreation preserved all 315 non-output study hashes. The final combined suite must be rerun at this candidate after the earlier in-flight run ends, because the docking fix postdates its boundary checks.

### Corrected-wheel clip/audit regression — 2026-10-03

The installed `6d5407a` wheel re-passed two-frame clip preview, GIF/MP4 download and metadata with all label options and rotation. GIF decoding returned 640×480 and two frames. System ffprobe confirmed H.264, 640×480 and two MP4 frames; ffmpeg fully decoded the MP4 with exit 0. Frame, selected residue, camera, display and Follow/playback/live-update/spin controls restored; all 315 non-output study hashes matched and no browser page errors occurred. Audit-bookmark restoration re-passed Preparation tag, event, stage pair, overlay, linked state and both cameras; its temporary QA row was deleted. These receipts apply to the corrected installed wheel, independently of the final combined suite still running.

### Corrected installed-wheel visual/context checks — 2026-10-03

The corrected wheel completed a 300-state control-geometry matrix: ten routes, Graphite/Ink/Paper, widths 1440/1280/1024/768/390 and text sizes 100%/200%; no out-of-viewport ordinary controls or page JavaScript errors were reported. Thirty route/theme screenshots were captured; Graphite Agent, Ink Viewer and Paper Overview were inspected directly. This geometry check is not a native OS popup or complete contrast audit. Installed browser review also refused acceptance after a controlled actual dashboard study update through `applyAppState`, without an acceptance HTTP request. The earlier attempt mutated the copied `state` getter and did not change live state. Separately, an installed-module controlled completion switched the server study before returning; the old answer was withheld. That is synthetic boundary evidence, not live provider switching. No scientific settings were applied.

### Frozen corrected-source combined gate — 2026-10-03

The required ten-module combined suite completed on the corrected source at `6d5407a` (subsequent commits are documentation only): 241 passed, 1 skipped, 5 warnings in 765.66 seconds. The skip is unavailable OpenFF toolkit; warnings are Pillow deprecations in clip assertions. JUnit receipt: temporary `FastMDXplora-agent-docking-final/combined-suite.xml`. Repository CI Ruff F/B and diff checks pass. The 30 installed route/theme screenshots were reviewed as contact sheets for page structure, with three representative originals inspected directly; this does not prove fine-text contrast in every pixel or native popup rendering. The direct OpenAI diagnostic completed but its harness incorrectly required `ok=true`; explanation replies deliberately return `ok=false` and an answer, whereas `ok=true` denotes accepted configuration. That failed assertion is not provider/auth failure evidence. A corrected bounded probe is in progress.

### Corrected-wheel OpenAI and persistence acceptance — 2026-10-03

A fresh direct NDJSON request through the installed `6d5407a` dashboard returned an explanation in 11.19 seconds with no error, action or configuration. Its answer distinguished saved preparation operations from inventory evidence and chemical-cause claims. Explanation replies deliberately use `ok=false`; an earlier harness incorrectly interpreted that as failure. The browser subscription status separately confirmed `selection=subscription`, provider `openai-chatgpt`, and GPT-6 Luna/max persisted after reload. The real context inspector confirmed selected-view evidence exclusion when Use current view was off. One earlier browser probe was interrupted after a prolonged harness wait and remains inconclusive; these bounded HTTP/UI checks supply independent completed evidence without claiming that interrupted probe passed. No credentials or account labels were recorded, and no scientific settings were applied.

### Native Windows option-menu acceptance — 2026-10-03

Windows.Graphics.Capture through the bundled computer-use skill exposed the actual native option popup on corrected installed wheel `6d5407a`, port 8787. Default protein-representation menus were visually inspected in Graphite, Ink and Paper: unselected options have black backgrounds/white text; the native selected highlight is light blue/dark text and readable. The subscription-model menu was also inspected in Paper, confirming the readable options and Astra → Sol 6.1 → Sol 6 → Luna 6 → remaining order. Popups were dismissed without selecting an option; Graphite was restored. No account/model/reasoning or scientific setting changed. This supersedes the earlier browser-only capture limitation for representative menus on this Windows host; it does not claim every OS/browser implementation or every individual menu was visually captured. The corrected-wheel review server on 8787 remains available; the user server on 8783 was untouched.

### Bounded full-preparation recorder measurement — 2026-10-03

The existing seeded protein audit-on/off equivalence fixture was executed with wall-clock measurement around the complete `a_real_setup` call on the explicit Reference backend, seed 314159. Audit off took 7.649 s; audit on took 7.552 s. The enabled journal and sixteen immutable snapshot files occupied 271,139 bytes (17 files total); disabled recording produced none. The existing exact input/prepared/topology PDB and System XML comparisons passed, as did positions/box comparisons with absolute tolerance 1e-10. The journal completed without warnings. Receipt: temporary `FastMDXplora-recorder-overhead-qnt_qruj/receipt.json`.

This is one paired small-protein preparation, not production MD or a statistical overhead benchmark. The negative timing difference is ordinary measurement variation and does not prove recording accelerates preparation. It adds full-preparation storage/timing evidence beyond the earlier single-observation POPC measurement; membrane/ligand overhead and broader backend gates remain qualified/open. No product source or scientific defaults changed.

### Shared relocated-topology installed acceptance — 2026-10-03

Behavior revision `a06a214` supersedes `6d5407a`; its final 244-case combined suite passed 243 tests with one OpenFF dependency skip in 799.87 seconds. A shared bounded verifier accepts the known local topology of a moved study only when its bytes exactly match the recorded original. Graph selection and server-side Agent evidence use this rule. Equal-sized changed bytes remain refused. Forty analysis/graph checks and four focused shared-verifier checks passed.

Installed wheel `fastmdxplora-2.5.9.dev228+ga06a214d2-py3-none-any.whl` has SHA-256 `46b7e468b8aed20824352a74185050257cb692d05e428ad7f0045f3868246a51`. A new temporary environment reused repository dependencies; no clean-machine claim. The completed-study browser selected ASN A:1, pinned it, selected LEU A:2 and sent a fresh-conversation OpenAI comparison. The reply correctly reported RMSF 0.131109476 and 0.0686216503 nm, CA scope, frame-0 alignment, 2,000 frames and missing uncertainty; chemical causes remained unproven. No browser errors occurred and scientific hashes matched, excluding expected conversation/bookmark/media outputs. No settings or simulation were applied. Receipt: temporary `FastMDXplora-relocated-evidence-final/graph-agent-receipt.json`. The remaining coherent bookmark/restart/media/audit chain is still open.

### Current installed residue-bookmark portability — 2026-10-03

The `a06a214` installed browser saved the LEU A:2 selection and pinned ASN A:1 comparison, reloaded, restored both exact identities, exported 4,281 bytes of JSON, previewed/imported it with skip-existing collision handling, and removed its QA bookmark. Earlier diagnostic QA rows were also removed. Scientific hashes matched and no page errors occurred. The harness now waits for the exact active study path before Restore; its earlier premature click was correctly refused by study isolation. This receipt proves reload restoration and JSON portability, not a new process restart or the remaining media/audit chain. Receipt: temporary `FastMDXplora-relocated-evidence-final/bookmark-flow-receipt.json`.

### Current installed restart, media and audit acceptance — 2026-10-03

The `a06a214` installed residue-bookmark flow passed an actual process restart: harness-owned server processes 30812 and 30488 exited normally. Browser restoration retained LEU A:2 and pinned ASN A:1, JSON export/import skip-existing handling passed, temporary QA rows were removed, scientific hashes matched and no browser errors occurred. Receipt: temporary `FastMDXplora-relocated-evidence-final/bookmark-restart-receipt.json`.

On the same corrected package and completed-study copy, clip preview/export produced two-frame 640x480 GIF and H.264 MP4 plus metadata. System ffprobe confirmed two MP4 frames and ffmpeg decoded it with exit 0. All requested label/rotation options were exercised; frame, residue selection, camera, display and playback controls restored. All 320 non-output hashes matched, with no page errors. The subsequent preparation-audit bookmark restored its Preparation tag, event, input/prepared stage pair, overlay, linked views and both cameras; its QA record was deleted. These are sequential installed acceptance phases on the same fixture, not one uninterrupted browser session. No scientific settings or production simulation were applied.

### Milestone review

| Milestone | Current state | Remaining boundary |
| --- | --- | --- |
| M0 — reconcile | Complete | Scope, branch base and physics/chemistry guardrails are documented. |
| M1 — context and human control | Implemented; OpenAI live route passed on exact wheel | Other subscription accounts remain external live dependencies. Human draft review, refusal and non-execution regressions pass. |
| M2 — bookmarks | Core portability passed on exact wheel | Exact source compatibility is regression-tested; broader study/backend combinations remain limited by the documented fixtures. |
| M3 — providers/models | OpenAI account, model order and reasoning verified | Claude, Kimi Code and Gemini adapters remain available but live sign-in/inference is UNVERIFIED because the user has no accounts for them. |
| M4 — clips | Exact-wheel GIF/MP4 preview, download, decode and state restoration passed | Encoder and browser differences outside this host remain environment-dependent. |
| M5 — provenance | Future-run observation and audit implemented; qualified scientific checks pass | Aggregate solvent/ion events do not identify each inserted molecule or retry. Broader backend/platform acceptance is open; do not claim universal chemical validation or deterministic CPU repeatability. |
| M6 — audit visuals | Exact-wheel audit bookmark and Agent handoff passed | Overlay remains display-only and only describes the evidence available in saved study files. |
| M7 — integrated release checks | Passed for the tested development environment and exact wheel | One OpenFF skip, external provider tests, and clean-machine installation remain unverified. |
| M8 — aesthetics/interactions | Source suite and exact-wheel dropdown styles pass | Representative native option menus now visually pass on Windows in three themes; platform-wide acceptance remains scoped to this host. |

### Remaining follow-up

1. If access becomes available, live-test Claude, Kimi and Gemini through their documented authentication flows. The user currently has OpenAI access only.
2. Native Windows representative option menus are now verified. Retain explicit host/browser scope; complete any remaining detailed visual-state requirements against the original contract.
3. Broaden scientific/backend acceptance only when the required dependencies and fixtures are available. Preserve the known CPU-repeatability and aggregate-provenance limits; do not alter scientific algorithms to hide them.

The source, tests and closeout receipts were pushed to `princeote/context-aware-agent`. Local and remote heads matched when verified. No upstream main integration or pull request was created.

README already points to this status page. The packaged Agent knowledge already contains the current scientific interpretation and human-control limits; no package-knowledge edit is needed. See [dashboard-next-goal.md](dashboard-next-goal.md) for the single operational checklist.

## Historical verification receipts and scientific limits

The following entries record earlier milestones; use the current checkpoint and milestone table above for the present acceptance state.

Earlier focused verification before the latest audit-bookmark regression: **65 passed in 113.23 seconds**
across `test_agent_reasoning.py`, `test_provider_connections.py`,
`test_research_bundle.py`, `test_clip_exports.py`, `test_preparation_audit.py`
and `test_preparation_recording.py`. This focused run verifies the existing
increments; it is not the outstanding complete M7 release pass.

Latest current-source boundary verification: **93 passed in 418.76 seconds** in
`tests/test_dashboard_agent_boundaries.py`, using the repository `.venv` with
Python 3.11.9. This closes the full source boundary-module receipt but does not
replace the frozen combined scientific/feature suite, final wheel installation,
or remaining native-popup and integrated-browser acceptance.

Most recent focused additions: the complete research-bundle suite passed **24/24**;
audit-bookmark round-trip passed **3/3** in Graphite, Ink and Paper. The combined
release set passed **241 tests, 1 skipped** in 856.07 seconds. The sole skip is
`test_openff_ligand_preparation_preserves_outputs_with_audit_on_or_off`, because
`openff.toolkit` is not installed in this environment. Five test warnings are
Pillow deprecations for `Image.getdata()` in clip assertions. Repository CI's
Ruff `F,B` checks pass; affected-test Ruff, JavaScript syntax and `git diff
--check` also pass. These source receipts do not replace final-wheel checks.

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

## Current closeout work

All open acceptance items, evidence requirements and the final branch sequence live only in [dashboard-next-goal.md](dashboard-next-goal.md). This evidence record does not duplicate that operational list.

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

### Combined regression and remaining presentation controls

The unchanged combined run at production/test revision 1276612 passes all 209
checks in 453.01 s with five Pillow deprecation warnings. Commit 5b254b9 only
added evidence documentation during the run. The preceding initialization
timeout remains in the record; the successful rerun does not establish its cause.

Comparison with the completion plan identified two remaining presentation gaps.
The clip dialog now groups Frames, Camera, Labels and Output, retaining all IDs,
options, bounds and rendering behavior. Guidance explains overlapping protein-wide
labels and the existing 200-label limit. Bookmark cards display saved/updated
times from their recorded timestamps, an explicit unavailable-date state,
screenshot placeholders, and per-card source-check status. The initial status
does not imply that sources were already verified; verification occurs on Restore.
These changes do not rewrite bookmark timestamps, source structures or exports.
All 53 clip and research-view tests pass in 146.49 s, including decoded media,
viewer restoration, three-theme layout/text contrast, populated/import/error
states and the integrated bookmark browser flow. Diff checks pass. The scientific
setup, backend, phase and analysis modules are unchanged since b103dd8, whose
protein/POPC/OpenFF preservation scope and limitations remain documented above.
Installed/native acceptance of this increment is recorded next. Later CSS
corrections require their own final installed build.

### Installed latest controls, media downloads and enlarged-text corrections

Native review recovered the installed 8d7adc5 dashboard on port 8784 in a fresh
review tab. The prior error document was stale; the existing server was reused,
not restarted. Bookmark cards show recorded saved/updated dates and initial
unverified source status. Restore changes the chosen card to matched-source
status and restores its RMSD range. Settings retains ChatGPT / GPT-5.6 Luna /
medium after the earlier server restart. The grouped clip dialog exposes Frames,
Camera, Labels and Output. At 390 px, its footer remains reachable; Escape closes
the dialog and restores focus to the export action.

A new native two-frame export at 640 by 480, 10 fps and 45-degree camera rotation
produced clip `66923f0fe138426cab0b2f8254868a2c`. The GUI's actual Download GIF
and Download MP4 links saved files whose hashes equal the study artifacts:
GIF `6ad5c51766d39df0d811305f26e329ad45f616a335cc8fe51d96530130e55867`;
MP4 `3c825052ac9d11f471c4db78b654e86b9899da3d9b278b2e95187cc388ec5e3c`.
Pillow decodes two 640 by 480 GIF frames of 100 ms each. ffprobe reports two
640 by 480 MP4 frames, 10 fps and 0.20 s; ffmpeg fully decodes without errors.
The sidecar matches preview browser frames 0/6, source frames 0/181 and times
0.0005/0.091 ns. Labels/captions and source fingerprints are retained. Protein-wide
residue labels can still overlap in this view; the dialog explains crowding and
offers selected-identity scope. All 309 baseline study files remain byte-identical.

Native enlarged-text inspection found a short study ID squeezed by the mobile
brand, an appearance popup extending above the screen with overflowing version
metadata, and a viewer status badge extending outside the canvas. The mobile
header now reserves study space before wrapping; the popup has bounded scrolling
and wraps rows/metadata; the viewer badge wraps inside the canvas. No scientific
values, camera state or account/model selection logic changed.

The new short-ID regression initially failed in all three themes at 200% / 390 px.
After correction, it and existing shell checks passed (6 checks, 48.53 s). The new
expanded-popup regression also failed in all themes before correction. Nine
header/popup/doubled-text checks then passed in 115.63 s. Six additional viewer
badge/contrast checks passed in 54.26 s. Ruff with the established E501 exception
and diff checks pass. Native temporary-style previews show the whole 1L2Y ID and
status, a bounded scrollable Paper popup at 200% / 390 px, and reachable citation
navigation. Temporary styles, text size and viewport overrides were removed;
the original Paper theme was restored. These previews are candidate-CSS evidence,
not a claim that the new CSS is already in the installed wheel. Full M8 visual
coverage and the final exact-revision installed release gate remain open.

### Installed enlarged-text build and mobile scrolling acceptance

Wheel `fastmdxplora-2.5.9.dev206+gdbfbf7c94-py3-none-any.whl` was built using
the declared isolated build dependencies (the source venv lacks a usable wheel
builder). SHA256:
`1954d100cc5ef1a5c600812ec0b65b9c4878d418ce83839cd26f099f1926a918`.
The existing isolated acceptance venv installs it successfully, passes pip check,
and verifies required assets and all 127 error references. Only the identified
8784 review server was restarted. The user's 8783 dashboard was preserved.

Native inspection at 200% text / 390 px verifies the new installed popup, wrapping
of its full development-version string, keyboard access to lower links, and the
complete playback/frame/time badge inside the canvas. These checks use shipped
CSS without injected candidate styles. ChatGPT / GPT-5.6 Luna / medium remains
selected after restart. All 309 baseline study files remain unchanged. Review-only
text-size and viewport overrides were reset, and Paper remains the selected theme.

Scrolling also revealed that the research toolbar and mobile navigation both
occupied the sticky top edge. Mobile research actions now stay in normal flow;
wide layouts retain their sticky toolbar. A source regression reproduced the
overlap in all themes at ordinary 390 px text before correction. Nine toolbar,
context-inspector and shell checks pass in 55.52 s. A strengthened three-theme
check then passes in 18.59 s, verifying actual body scrolling, study identity
hit-testing at partial scroll, bookmark opening, Escape and focus return at
100%/200% and 390/768 px. An initial window-scroll probe did not move the actual
body scroll container and is excluded as scroll evidence. A subsequent wait for
DOM hiding timed out because the existing loader fades with opacity and disables
pointer events; waiting for the application's ready state corrected the harness
without changing loader behavior or dropping the scroll/hit assertions.

A native temporary-style preview corroborates unobscured Paper study identity
and navigation at 200% / 390 px after scrolling; that preview was removed. The
mobile-toolbar correction postdates the dbfbf7c installed wheel and needs final
installed acceptance. Ruff and diff checks pass. Full remaining M8 surface/state
review, integration and the final frozen release gate remain open.

### Earlier 2026-10-03 responsive checkpoint — superseded

This historical receipt predates the v7 review and the current source navigation-focus correction. The current candidate identity, remaining visual/package gates and branch boundary are summarized in the checkpoint above. Port 8783 remains untouched; installed integration, frozen release checks and branch handoff remain open.
