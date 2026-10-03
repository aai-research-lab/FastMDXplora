# FastMDXplora dashboard implementation framework

Status: implementation resumed after user approval. Milestones are committed and pushed after verification.
This document replaces the preliminary plan using the user's clarified requirements.
Working checkout: `C:\Users\User\OneDrive\Documents\GitHub\FastMDXplora`.
Branch: `context-aware-agent`, tracking `princeote/context-aware-agent`.
User instruction (2026-10-02): keep the existing branch base and all feature
changes on princeote/context-aware-agent; do not integrate upstream main.
The initial provisional edits are being reconciled into verified milestones.

## 1. Product contract and priorities

Build five connected additions: an explanation-first context-aware Agent, research
bookmarks, MP4/GIF trajectory clips, preparation transparency, and provider sign-in.
The dashboard must remain useful without a model subscription or network access.

The Agent can explain and propose a draft. It cannot decide to run a simulation,
apply a scientific setting to an active study, change source artifacts, or accept
its own proposal. Every transition from a suggestion to an applied setting or
execution requires a deliberate human action. The new dashboard flow will not
offer autonomous or unvalidated execution as an Agent mode.

Priority order: approval boundaries and shared context; Agent knowledge; bookmarks;
provider connections; clips; preparation recording and audit visualization.
Provider research starts early because support differs by provider. Authentication
does not block work on the other features, which can use synthetic model replies.

The preparation audit is secondary: an expandable panel under setup/structure
information, collapsed by default, with an optional larger comparison dialog.
It does not become the dashboard landing page or the main navigation destination.

## 2. Non-negotiable scientific boundaries

- Preserve scientific algorithms, defaults, validation, refusal conditions, atom
  order, chemical identities, force-field resolution, unit conventions, numerical
  parameters, random seeds, simulation scheduling and analysis definitions.
- Changes inside setup are limited to observing actual operations and recording
  provenance. Do not execute an operation twice, rebuild a structure to obtain a
  picture, consume random numbers, or alter an in-memory scientific object.
- Keep deposited/input coordinates, prepared structures, topology, trajectory,
  system/state XML and existing manifests immutable to dashboard display/export.
  New audit metadata and explicitly labelled snapshots are separate outputs.
- Do not treat AI explanations as scientific validation. Keep existing warnings
  visible; unknown evidence remains unknown even when a model is connected.
- Track units and frame identity explicitly. Viewer/browser frame indices may
  represent a sampled subset of a scientific trajectory. Playback speed is a
  presentation setting and never changes the physical times in the source.
- Distinguish confirmed events, differences inferred from saved files, ambiguous
  mappings, requested settings, resolved settings and unrecorded information.
- Report any pre-existing scientific issue separately. A feature implementation
  must not silently bundle a change to physics or chemistry.

Testing is planned below for the later validation stage. This planning step does
not launch simulations, authenticate accounts, or install provider clients.

## 3. Shared architecture and context contract

Extend the existing Python GUI server, JavaScript dashboard, molecular viewer and
Agent completion interface. Avoid a second application or duplicate scientific
pipeline. Keep provider-specific authentication behind provider adapters.

Introduce one versioned study-view context shared by Agent, bookmarks and clips:

| Context field | Purpose | Server verification |
| --- | --- | --- |
| Schema version, study identity, request generation | Prevent mixing studies or late responses | Active runtime and source fingerprints |
| Page and selected setting | Explain the current task/field | Known page and configuration schema |
| Analysis ID, graph range, axes and units | Explain selected metric and region | Analysis catalog and saved series |
| Warning ID and source | Explain an actual warning | Current structured warnings/events |
| Atom/residue selection | Resolve the selected molecular identity | Source topology, chain, residue number, insertion code, atom, alternate location |
| Browser frame, source frame and simulation time | Identify what is being displayed | Playback index and source signature |
| Camera and viewer display state | Reproduce a view | Bounded numeric and enum values |
| Preparation event ID | Explain a selected preparation change | Audit record for this study |

The browser sends identifiers and view hints. The server retrieves the relevant
facts from the active study. It does not accept browser-supplied text as verified
scientific evidence. Bound payload sizes, omit unrelated results, and invalidate
context when studies change. A graph range does not silently change full-series
means or uncertainty; explicitly state the scope of statistics in the response.

Small, separate services will handle context/evidence, bookmarks, clip jobs,
preparation audit and provider connections. Existing modules may remain the
implementation homes where that avoids unnecessary churn. Exact filenames are
chosen after the focused code review, not treated as a new architectural mandate.

## 4. Context-aware Agent and its Markdown knowledge file

### Required behavior

The sidebar is available on every page, opens/closes without losing the current
page, and has a persistent user preference to disable it entirely. On a narrow
screen, use a drawer so graphs and the molecular viewer remain usable. Closing or
disabling it stops new requests; cancellation of an in-flight reply is explicit.

Show a compact context line, such as `RMSF · residues A:40–A:75` or
`Viewer · source frame 240 · GLU A:57`. Let the user inspect the exact context and
choose whether a message uses it. Keep conversation context scoped to the study.
Examples include explaining a peak, warning, selected setting, preparation event,
or what can and cannot be concluded from one trajectory frame.

### Knowledge contract

Add a packaged, versioned Markdown reference, provisionally
`src/fastmdxplora/agent/knowledge/fastmdxplora.md`, with:

1. The setup → simulation → analysis → report workflow and dashboard pages.
2. Configuration names, units, defaults and schema references.
3. Structure preparation, assembly, missing atoms/residues, heterogens, ligand
   handling, pH/protonation, solvation and force-field concepts.
4. Analysis metric meanings and limitations; warnings and refusal code guidance.
5. Topology/trajectory atom mapping, sampled playback frames and simulation time.
6. Evidence locations and source-link conventions for each type of explanation.
7. Human approval rules and the distinction between a draft and an active study.
8. Missing-evidence behavior, uncertainty and scientific interpretation limits.

The installed application must be able to load the file outside a source checkout.
Read it through package resources and include it explicitly in build packaging.
Use schema-generated facts where possible and maintain curated explanatory text
for concepts. Record the knowledge version and compatible context schema. Keep a
lightweight consistency check so field names and units do not drift from code.

Send relevant knowledge sections plus bounded current evidence, rather than the
whole codebase. Stable software knowledge comes from Markdown/schema; run-specific
facts come from artifacts. User notes, imported bookmarks and provider output
remain untrusted content. The knowledge document is maintained with feature
changes and is not modified by the Agent during a conversation.

### Human approval path

Use: explain → suggest → user selects `Add to draft` → show a configuration diff
and scientific implications → existing validator checks the draft → user reviews
the finalized configuration → user explicitly applies/saves or launches it.

The Agent model has no execution/write tools. Provider SDKs must disable shell,
file editing, background agents and arbitrary tool execution. A prompt instructing
the model to avoid actions is insufficient. Enforce allowed capabilities server
side and reject autonomous/unvalidated modes in the new dashboard Agent path.
Validate approval against the exact draft and study; editing either invalidates
the previous approval. Ordinary human run-builder controls stay available.

Acceptance: all eight context types are evidence-linked; missing evidence is
stated; hidden/disabled Agent creates no new requests; draft suggestions do not
touch the active study; a model response cannot start a simulation.

## 5. Research bookmarks

### Capture and organization

Save a title, note, creation/update times, tags, study/source identity, and the
applicable portion of the shared view context. Offer predefined tags:
`Simulation settings`, `Graph`, `Figure`, `Trajectory frame`, `Structure`,
`Preparation`, and `Observation`. Allow multiple tags and filter/search by title,
tag and note. Suggest tags from the current view without silently overwriting
the user's choice. Editing/deleting affects only the bookmark.

Offer an optional screenshot thumbnail. Graph screenshots capture the graph;
viewer screenshots capture the molecular view; settings bookmarks capture only
the relevant panel. Exclude chat, authentication dialogs and unrelated personal
information from default captures. An unavailable screenshot does not prevent
saving a valid text/view bookmark.

Viewer representation means the way molecules are drawn, such as ribbons or
sticks. Visibility means whether water, ions, hydrogens or a ligand are shown.
Save these quietly so a restored screenshot/view is meaningful. Do not introduce
a color-customization workflow; retain only existing appearance state where
needed to reproduce the view.

### Placement and restoration

Expose a compact study-wide bookmark list plus contextual `Bookmark this` actions
on graphs, figures, viewer frames, settings and audit events. Do not place save
controls on login/error/empty screens where there is no research view to capture.
Use a persistent panel/drawer without crowding existing navigation.

On restore, select the correct page and available data, restore range/camera/
selection/display state, and show the note. Verify study, source fingerprint,
topology identities and browser-to-source frame mapping before restoring. If a
source changed, preserve the note/screenshot and explain what cannot be restored.
Do not silently select an unrelated residue or approximate a missing frame.

### Persistence and portability

Keep versioned bookmark JSON and screenshot files under `.research/`. Use atomic
writes, stable IDs, concurrent-edit protection and relative references. Export a
portable bundle containing JSON, screenshots and source identity metadata; also
offer JSON-only export. Import previews the records, checks version/size/path
safety, handles ID collisions, and distinguishes compatible from unavailable
views. Unknown tags can be retained as text; executable actions and external file
paths cannot be imported. Do not overwrite existing notes without explicit choice.

Acceptance: persistence across restart; edit/tag/search/delete; screenshot;
compatible restore; explicit stale-data handling; export/import on another path;
no scientific output or credential inclusion in the bundle.

## 6. Trajectory clip export

Add `Export clip` to the molecular viewer. It opens a dialog with start/end browser
frames, stride, resolution, frames per second, format, camera behavior and labels.
Show source frame/time equivalents and a preview/estimate before export.

Formats: MP4 and GIF. Offer fixed camera and deterministic camera rotation, with
direction and total angle; use the current view as the starting orientation.
Camera rotation changes the rendering only. It never rotates saved molecular
coordinates, alters dynamics or interpolates molecular positions.

Offer independent checkboxes for residue names, selected atom labels, frame
number, simulation time, structure/study title and custom caption. Keep labels
legible by using selected/highlighted atoms/residues initially; an all-labels
option warns when it will crowd the image. Avoid assigning one frame's labels to
different atom identities in a later frame. Show the simulation-time unit.

Render only saved playback frames, using the existing viewer's mapping. Provide
progress, cancellation and size/frame limits. Pause automatic playback/live
following during capture, freeze the source identity, and restore the user's
original frame, camera and controls afterward. Refuse or cancel if the study or
source mapping changes. Restore controls even on encoder/rendering failure.

Encode GIF using a supported image encoder; MP4 requires an available encoder
such as FFmpeg. Detect it before starting and show an actionable availability
message. Do not report MP4 support solely because its button exists.

Save the completed media and a provenance sidecar under `exports/clips/`, then
offer download links. If both formats are selected, keep them in one named export
record. The sidecar records browser/source frames, physical times, sampling,
source signature, camera path, labels, display state, fps and encoder. Repeated
exports create distinct names and never overwrite source data or previous clips.

Acceptance: both formats decode and play; frame order/stride/labels/time match the
source; rotation is smooth and presentation-only; save/download agree; cancel and
failure restore viewer state; no export mutates topology or trajectory.

## 7. Preparation provenance and visual audit

### Evidence for old and new studies

For existing studies, read available input/prepared/system structures and setup
records. Label saved input accurately: it may already reflect model/assembly
selection and must not be called the untouched deposition without evidence.
Differences alone establish inventory changes, not their chemical cause.

For future runs, preserve a source snapshot before existing selection/conversion
operations where feasible, with source format/identity and checksum. Record
actual events around existing operations: model/chain/assembly selection,
mutations, missing-residue/atom repair, hydrogen/protonation handling, heterogen
retention/removal/reinstatement, ligand parameterization, solvation/ions, and
resolved force-field choices. Label generated sequence inputs as generated.

Use a versioned `setup/preparation_audit.json` and bounded separate snapshots
under `setup/audit/`. Each event records operation, ordering, before/after source
references, actual atom/residue identity mapping where known, user choice versus
resolved choice, recorded reason, backend/version and relevant warnings. Record
counts separately from optional full snapshots, and mark unavailable details.

Record reasons at the existing decision point; never reconstruct them from an AI
guess. Use the operation's existing result rather than rerunning it. Protect
performance by streaming/hashing files and avoiding full solvent-heavy copies
for every event. Snapshot limits must produce an explicit incomplete-audit status,
not silently truncated structural evidence. Audit-capture failures are visible
and do not alter scientific settings or conceal an existing pipeline failure.

### Reusable visuals

The audit works without AI using one deterministic template:

1. **Stage strip:** source → selected assembly → repaired solute → prepared system,
   with unavailable stages labelled.
2. **Side-by-side structures:** original/source and prepared stage with linked
   camera controls, selected changes and matching residue identities.
3. **Optional overlay:** align only confidently matched atoms; state the atom
   set and that alignment is for display. If correspondence is ambiguous, do not
   offer a misleading overlay or structural RMSD comparison.
4. **Inventory bars:** protein/ligand/water/ion counts by stage, separating added
   solvent from repaired solute atoms.
5. **Affected-residue track:** chain/residue positions with categories for repair,
   mutation, component removal and recorded state changes. Use insertion codes.
6. **Decision table:** pH/explicit protonation choices, assembly, heterogen handling,
   force-field XMLs and ligand parameterization, with recorded reasons and sources.

Do not use invented pre-simulation energy graphs or label hydrogen counts as proof
of a protonation state. Count differences do not certify preparation quality.
For numbering changes, alternate locations, assembly copies or incomplete maps,
show the uncertainty and use the relevant stage viewer for selection.

Clicking a bar, residue marker or event highlights the corresponding structure,
shows before/after evidence and source, and offers `Ask Agent about this change`.
The Agent receives the event ID and server-verified record. Without a provider,
the same factual template explains the recorded operation and its limitations.

Acceptance: historical studies have honest missing-evidence behavior; new events
match the actual operations; clicks focus correct identities; known atoms align
correctly; ambiguous maps are refused; audit on/off does not change scientific
outputs for an equivalent seeded run on the same backend/platform.

## 8. Browser-initiated provider connections

The requested experience is: dashboard Settings → choose provider → `Connect` →
provider-owned browser authentication/consent → dashboard confirms the account
and available models. Password/MFA entry stays on the provider's page. Show login
progress, cancellation, expiry, reconnect, account selection and disconnect.

Provider coverage targets OpenAI/ChatGPT/Codex, Claude, Kimi Code and Google
Gemini. Treat agents/clients and models as separate concepts:
connecting a coding agent does not give that agent permission to edit files.
Add further providers through the same documented adapter contract. Do not label
an API-key connection or ordinary account sign-in as subscription inference.

| Provider | Planned route | Verification required before claiming support |
| --- | --- | --- |
| OpenAI/ChatGPT/Codex | Public Sign in with ChatGPT for eligible local/open-source apps; Responses API | Dynamic registration, PKCE/state/nonce, identity, scopes, model catalog, completed inference, renewal |
| Claude | Provider-supported official-client/SDK route with browser login | Current third-party/subscription rules, tool-free operation, account-specific inference; no copying private client tokens |
| Kimi Code | Official client-managed device/browser login and supported integration | Login lifecycle, cancellable bridge, tool-free inference and plan entitlement; direct third-party OAuth is not assumed |
| Gemini | Provider-supported Google login/client integration | App registration requirements, supported headless transport, account entitlement and restricted capabilities |

OpenAI's current public docs describe direct OSS OAuth and Responses API plan
usage. Kimi documents
OAuth for official clients and API keys for third-party applications. Claude and
Gemini routes require a focused compatibility check before selecting transport.
Document the exact verified support rather than promising universal OAuth.

For each adapter: identify who owns credentials, what installation/registration
is required, whether the dashboard can initiate the browser flow, whether the
connection permits model inference, and whether tools can be fully restricted.
An unsupported direct flow remains explicitly unavailable; an official bridge
is acceptable only if it delivers dashboard-initiated login without changing
the user's unrelated client account or granting execution permissions.

Keep credentials outside studies/repos/browser storage using protected OS storage
or the official client's own credential manager. Never scrape/copy credential
files, embed another application's private client identity, log secrets, or
silently select an API-billed fallback. Provider/model/account selection is
explicit; usage/limits are shown when a supported provider endpoint exposes them.

Allow API-key connections as a separately labelled existing option. Expired
subscriptions, denied consent and unsupported entitlements remain clear failures.
Local desktop support is the first target; a remotely hosted multi-user dashboard
needs separate per-user credential isolation and provider eligibility verification
before enabling these sign-in controls there.

Acceptance: successful login alone is not sufficient. Each provider is complete
only after approved model enumeration, a harmless explanation response, expiry/
cancel/disconnect behavior, restricted tool checks and verified credential isolation.
Real account login is performed by the user at the later test stage.

Official references used for provider feasibility:

- [OpenAI OSS registration/sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [OpenAI model catalog and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Claude programmatic operation](https://code.claude.com/docs/en/headless)
- [Claude third-party login guidance](https://support.claude.com/en/articles/13189465-log-in-to-your-claude-account)
- [Kimi membership and integrations](https://www.kimi.com/en/help/kimi-code/membership-guide)
- [Kimi login/client command](https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command.html)
- [Gemini authentication](https://geminicli.com/docs/get-started/authentication/)

## 9. Implementation milestones and review gates

| Milestone | Work | Deliverable / exit condition |
| --- | --- | --- |
| M0: reconcile | Inventory current edits; compare them against these requirements; identify existing autonomous paths and incomplete code | Change map, data-flow review, current limitations; no assumption that early code meets the final requirements |
| M1: context and boundaries | Shared context, packaged knowledge Markdown, evidence resolution, approval controls, sidebar disable preference | Every requested context type represented; explanation/draft paths cannot execute |
| M2: bookmarks | Tags/search, screenshots, display restoration, versioned bundle import/export, stale checks | Portable round-trip and usable list/drawer on relevant pages |
| M3: provider connections | Compatibility matrix, restricted adapter interface, browser login lifecycle, OS storage, account/model picker | Provider-by-provider support with honest capability states; other features independent of network |
| M4: clips | Export dialog, selectable overlays, camera rotation, source freeze, encoders, progress/cancel, save/download | Reproducible render metadata and valid MP4/GIF artifacts |
| M5: audit recording | Observe existing setup operations, source preservation, versioned events/mapping, performance limits | Actual future-run provenance; old runs remain readable |
| M6: audit visuals | Expandable panel, stage template, side-by-side/overlay, inventory/residue figures, Agent evidence links | Consistent factual audit with and without a connected model |
| M7: integrated validation | Tests below, manual browser review, docs, packaging checks, final diff review | Evidence-backed release checklist and known limitations before any commit/push |

Within each milestone: inspect the relevant existing path → specify the data/UI
contract → implement the smallest complete behavior → verify failure and stale
cases → update knowledge/docs → review the milestone. Do not introduce all changes
in one unreviewable patch or keep extending a prototype without updating its scope.

## 10. Later validation framework

Use synthetic provider replies and small fixtures first. Real provider sign-in,
new simulations and the broader scientific checks occur in the later validation
stage requested by the user. Document what is untested until then.

| Area | Representative acceptance check |
| --- | --- |
| Human control | A reply proposing a run cannot launch it; autonomous payload rejected; changed drafts require fresh human review |
| Context | Switch page/study while a response loads; selected warning/metric/frame/event resolves to the correct study |
| Knowledge | Installed wheel loads Markdown; field/unit references match schema; unknown findings are not invented |
| Bookmarks | Restart, edit, filter, screenshot, export/import, changed path, missing/changed frame, duplicate IDs and malformed bundle |
| Clips | GIF/MP4 decode; source frames/time/stride agree; label selection and rotation work; errors/cancel restore viewer |
| Audit | Known repair/removal/protonation/assembly records; historical missing sources; insertion codes, alternates and duplicated assemblies |
| Scientific preservation | Same seeded inputs/backend/platform with audit disabled/enabled; compare topology/atom ordering, parameters, constraints, seeds and scientific outputs with justified numerical tolerances |
| Provider | Denied login, wrong state/nonce, expired tokens, refresh, account switch, missing client, quota failure; no write/shell tools |
| Server/storage | Origin/host/public access gates, bounded uploads, path/symlink containment, concurrency, interrupted writes, no credentials in exports |
| Browser | Sidebar disabled/open, narrow viewport, keyboard focus, expanded audit, clip dialog and bookmark interactions |

Use the current study for display-only acceptance. Add protein-only and ligand
fixtures for mapping/chemistry; add a membrane fixture where relevant for solvent,
ions and component counts. Do not launch a new production MD study merely to test
a UI control. Run existing relevant scientific and dashboard suites after feature
tests; expand testing when failures or scientific instrumentation justify it.

## 11. Completion definition and status tracking

Residue-comparison follow-up: the molecular viewer can pin one clicked residue,
select another, and prepare an unsent comparison question. Both identities are
independently checked against the study topology and per-residue analysis.
Pinned comparison state is included in bookmarks, cleared on study changes,
and restored with saved viewer state. Same-residue comparisons are disabled.
Missing/ambiguous mappings have no invented measurement, and measured RMSF
differences are explicitly distinguished from possible chemical mechanisms.
Validation: 83 Agent/bookmark/import-export/script checks passed; after extending
the browser flow, both targeted evidence and native-click/restore checks passed.

Maintain a requirement-to-milestone checklist with `planned`, `implemented`,
`verified`, and `blocked` states. A blocked provider is named with its exact
dependency or unsupported route; a login button does not count as verification.
Existing preliminary tests do not establish that the newly expanded scope passes.

Completion requires: requested controls and saved artifacts, contextual explanations
with source evidence, human approval enforcement, package-installed knowledge,
bookmark portability, valid media, accurate old/new audit behavior, provider
capability verification, preserved scientific behavior, updated documentation and
a reviewed branch diff. Report remaining limitations explicitly.

User approved implementation and requested a GitHub update after each verified milestone.
The Agent must cover all registered errors and actual diagnostics, handle unfamiliar external
errors honestly, and support meaningful clickable protein-residue explanations.
Keep scientific algorithms unchanged; inspect each milestone before commit and push.

### Verified milestone M1 — context, knowledge and human control

- Packaged Markdown workflow knowledge and a generated reference covering every
  registered refusal; unfamiliar external failures require actual diagnostics.
- Shared current-study context includes graph ranges, selected settings, recorded
  warnings, source frame/time, and verified molecular identities. Missing or
  ambiguous residue evidence is stated explicitly.
- Protein-residue clicks highlight a residue and offer an explanation prompt.
  Existing atom measurement remains available. Asking does not send until Send.
- Agent proposals require an explicit Add to draft action; the Agent execution
  endpoint refuses execution. Autonomous/unvalidated dashboard modes are closed.
- Sidebar availability and its disable preference persist across pages.
- Preliminary study-local bookmarks and graph ranges establish the shared view
  foundation; tags, screenshots and portable import remain milestone M2 work.
- Validation: 233 Agent/context/research tests passed; 46 browser/public-dashboard
  checks passed and one skipped; 22 viewer measurement/selection tests passed.
  Ruff F/B and diff checks passed. A built wheel includes the Markdown resources.
- Live acceptance used the existing completed 1L2Y study on port 8780 without
  launching a simulation. Provider-backed explanations await milestone M3.
- Clip export and preparation-audit prototypes are excluded from this milestone.

Residue evidence follow-up: verified RMSF options now gate residue assignment.
Per-atom or unknown table granularity is refused. Tables without chain columns
map only when the residue number identifies a unique topology residue; duplicate
numbers across chains remain ambiguous. Evidence includes recorded atom selection,
alignment reference, frame count and sampling fields without filling missing
metadata with assumed defaults. Twenty-two Agent-boundary checks passed. The
completed 1L2Y study returned ASN A:1 = 0.131109476 nm and LEU A:2 = 0.0686216503 nm
from its per-residue table; topology, trajectory and RMSF source checksums stayed
unchanged. These measurements do not establish a causal chemical explanation.

Graph identity follow-up: RMSF graphs now distinguish recorded per-atom,
per-residue and unverified-index profiles. Atom/unknown profiles have matching
axis/tooltips and cannot focus a residue or trajectory frame. Missing chain
columns are resolved only against unique saved analysis-topology identities;
ambiguous/unavailable mappings remain unfocusable with an explicit notice.
Cropping now slices residue identities alongside plotted values and labels, so
clicking a cropped point cannot focus a different residue. Fifty-seven graph
and Agent tests passed, including native browser tooltip, focus and crop checks.
Only dashboard readers/rendering changed; RMSF calculations and source values
were not altered. Upstream main was inspected but not integrated, per the user's
instruction to retain the existing princeote branch base.

### Verified milestone M2 — portable research bookmarks

Implemented preset/custom tags, title/note/tag search, tag filtering, saved viewer
representation/color/visibility, and optimistic edit/delete conflict checks.
Optional PNG thumbnails are normalized without embedded metadata, bounded by
dimensions/bytes/storage, and served only through the private study API.
Graph captures use the offline bundled html2canvas 1.4.1 MIT dependency; modern
CSS colors are normalized only in its private rendering clone. Viewer capture
uses the molecular renderer's PNG output. Text bookmarks survive unavailable
capture. JSON metadata and ZIP screenshot bundles support filtered exports and
preview-before-apply imports with copy/skip/replace choices. Imports are bounded,
path-contained, and reject stale study/revision changes. Source fingerprints
protect graph, figure, topology and trajectory restoration; changed sources
retain notes/screenshots without silently selecting different scientific data.
Contextual save controls cover graphs, report figures, settings and viewer frames.
Playback restoration preserves camera, whole-residue selections and display state.
Live polling cannot overwrite a selected playback frame, and workflow telemetry
cannot replace its physical timestamp with the total study duration.
Preparation-event bookmark controls will connect to milestone M6 audit records.

Validation: 113 dashboard/viewer/public-access checks passed with one skipped;
63 final bookmark/portable-bundle/Agent-boundary checks passed. Ruff F/B and diff
checks passed. Nine new/updated package resources were compared byte-for-byte
against the built wheel. Scientific preparation/simulation/analysis modules are
unchanged; verification uses completed studies and fixtures without a new MD run.
Provider connections, clip export and preparation audit remain future milestones.

### Milestone M3 — provider connections in progress

User revoked the milestone stop on 2026-10-02. Continue through M3 connections,
M4 clips, M5/M6 preparation provenance and audit, and M7 final verification.
Publish each verified milestone; keep the full goal active until completion.
Do not treat a partial authentication helper as the completed M3 milestone.

Implemented single-use OpenAI authorization transactions, a private loopback
listener, signature/issuer/audience/nonce/expiry validation, renewable session
transport and tool-free Responses streaming. Windows storage uses current-user
DPAPI; macOS/Linux require a native OS keyring with no plaintext fallback.
Dashboard Settings now offers browser sign-in, cancellation, connected-account
and model selection, and disconnect/revocation. Subscription failure never
selects an API-key fallback. Account/model changes invalidate stale replies;
draft provenance records the actual selected subscription model.

Validation so far: 55 service/transport/identity/Agent-boundary tests passed;
seven connection-service tests, including a fixture-provider browser sign-in,
passed after dashboard integration. Signature tests initially exposed a missing
cffi binary in the local venv; reinstalling its Python 3.11 wheel fixed imports
and all signed-token tests passed. No actual account credentials were used.

Live OpenAI discovery currently fails TLS validation because the presented
certificate is expired. Certificate verification remains enabled; real sign-in
cannot be claimed verified until this environment issue is resolved.
Claude now uses an isolated official-client profile, personal Pro/Max account
checks, safe mode, disabled hooks/MCP/slash commands and an empty tool list.
Managed host policies are refused because they outrank client flags. Kimi uses
an isolated official-client profile for device OAuth and an explicitly bound
tools-empty agent file for inference. Its default REST sessions were found to
enable 25 tools, so they are never used for dashboard explanations. A native
Kimi inference test against a synthetic local endpoint verified zero tools.
Device sign-in URLs, account metadata, model selection and logout are wired to
Settings. Credentials remain with the official clients outside study data;
their Windows/Linux storage is not represented as DPAPI encryption.

Latest adapter validation: 125 provider, Agent-boundary, cancellation and script
checks passed, including native Kimi and Gemini zero-tool inference fixtures and
a fixture-provider browser OAuth flow. Gemini uses the verified official CLI
0.62.0 core content generator with no agent session, tools, hooks or MCP. Its
separate profile uses the official encrypted file storage, with native-keychain
fallback explicitly refused. Only existing Code Assist enrollment and returned
account models are used; there is no Cloud-project selection, new enrollment,
API-key fallback or automatic credit overage. Removing the local Gemini login
does not confirm revocation of Google's grant. OAuth connections expands to one
provider dropdown for ChatGPT/Codex, Claude, Kimi and Gemini, and one contextual
Connect button. Connected-account models are selected after login. Actual subscription-account consent/inference
remains unverified; the user must perform normal provider sign-in.

OpenAI's expired presented certificate still prevents live
verification; TLS verification is never disabled. Browser disconnects now signal
cancellation to buffered provider processes through streaming heartbeats.

Provider error follow-up: subscription failures now preserve registered codes
for refused sessions, usage/rate limits, or an unclassified connection failure.
They are no longer all labelled missing API keys. The packaged reference was
regenerated and covers all 127 current codes. Fifty provider/Agent tests passed,
including HTTP 401/403/429/503 classification and no API-billing fallback.

Package follow-up: the wheel built at 841ae217d contains the current 127-code
Markdown error reference byte-for-byte, residue pin/compare controls, the provider
dropdown and Gemini bridge. This verifies packaging, not user-account inference.

Integrated checks: 338 passed with one skipped across Agent human controls,
bookmarks/import-export, native clips, audit comparisons and provenance,
streaming cancellation, browser scripts and private/public dashboard routes.
After the dropdown change, 32 connection/browser and script checks passed.
The built wheel contains the native Gemini bridge and provider UI resources.
The real 1L2Y dashboard was refreshed and the provider selector inspected; no
actual provider login or new production simulation was initiated.

### Verified milestone M4 — trajectory clip export

Viewer exports GIF, MP4 or both together from 2–120 saved browser frames, with
first/last/stride, presentation fps, deterministic camera rotation, and checkbox
controls for residue names, atom names, source frame number and recorded time.
Selected-residue/atom or protein label scope is bounded to 200 labels. MP4 uses
ffmpeg/libx264; GIF uses Pillow and records its actual centisecond timing.
Exports are saved under unique study-local exports/clips directories with source
checksums, browser/source frame mapping, physical timestamps, camera path, labels,
display state and encoder metadata. Scientific coordinates are never interpolated
or written. Source changes during export refuse the result. Cancel/failure
cleans uploads and restores the previous viewer state; polling and keyboard
input cannot overwrite the scene while frames are being captured.

Validation: 55 clip/viewer/public-dashboard checks passed with one skipped; an
additional browser cancellation test passed. Both MP4 and GIF decode; a real
browser exported a rotating residue-labeled clip and restored the original
structure view. Trajectory/topology checksums stayed unchanged. Live acceptance
on the completed 1L2Y study saved both formats together without a new MD run.
Ruff F/B and JavaScript syntax/diff checks passed. Provider connections and
preparation provenance/audit remain in progress; this independently complete
visualization milestone does not claim the full framework is finished.

### Verified milestone M5 — future preparation provenance

New preparations record observed input/model/assembly choices, heterogen policy,
PDBFixer mutation and repair stages, requested protonation settings, prepared
solute/system snapshots, and existing resolved setup choices. Snapshots are
immutable per preparation and checksum identified. Storage limits and recording
failures are explicit; recording never supplies scientific settings or replaces
backend failures. Historical studies are not retroactively claimed recorded.
Aggregate system snapshots do not claim every solvation/ionization step was
individually observed. The record does not certify chemical suitability.

Validation: five recorder checks passed, including a full seeded preparation
with audit on/off on OpenMM Reference: input/prepared/topology PDB and System XML
were identical, and positions/box matched within 1e-10. Initial automatic-device
comparison differed slightly in hydrogen coordinates; preservation verification
is limited to the explicit Reference backend, not a general GPU determinism
claim. Existing setup suites passed after repairing an incompatible local OpenMM
binary with the same 8.5.2 Python 3.11 wheel. No production MD was launched.

### Verified milestone M6 — optional preparation comparison

The overview has a collapsed preparation audit with saved-stage inventories,
side-by-side viewers, linked rotation/zoom and an optional display-only alignment.
Exact heavy-atom correspondence is required; duplicate identities, missing or
changed snapshots, unsupported formats and collinear fits refuse overlays.
Water/ion differences are grouped for readability. Clicking atoms supplies
server-verified saved-stage context to the explanation Agent. Bookmarks preserve
stage pair, camera views, overlay and selected atom with source fingerprints.
A historical inventory difference is explicitly distinct from a recorded event.

Validation: six audit checks passed, including browser comparison/overlay and
Agent selection restoration, checksum/path guards, read-only coordinates and
bookmark invalidation when a displayed stage changes. The 31 audit/bookmark
checks passed before the last inventory grouping; 44 Agent/public-route checks
passed with one skipped after grouping. Real 1L2Y saved-stage comparison was
inspected in the dashboard without launching a new simulation.
