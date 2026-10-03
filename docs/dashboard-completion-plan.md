# Dashboard completion and aesthetics plan

Prepared 2026-10-02 from the [current milestone audit](dashboard-feature-status.md),
the [original framework](dashboard-feature-plan.md), code inspection and the
running completed-study dashboard. This is a completion plan, not a claim that
the remaining features or redesign have been implemented.

## Outcome and scope

Finish every outstanding requirement, make the dashboard visually coherent, and
produce evidence for a reviewable branch. Preserve milestone IDs M0–M7 and add
**M8: aesthetics and interaction design**. M8 runs before the final M7 release
gate even though its ID is higher. Existing working features remain the base;
their final regression checks remain mandatory.

Use the GitHub Desktop checkout on `princeote/context-aware-agent`, retaining its
v2.5.8 history without integrating upstream main. Publish a coherent description
and verification record after each completed increment. Keep the current status
document accurate whenever requirements or evidence change.

The scientific boundary applies to every milestone: no change to scientific
algorithms, force-field selection, protonation decisions, defaults, seeds, atom
ordering, units or analysis definitions. UI actions cannot rewrite scientific
inputs/results. An Agent can explain and propose a draft; humans review and
initiate scientific actions. Any discovered scientific defect is reported
separately from this feature work.

## Milestone sequence and completion tracking

| Milestone | Starting state | Remaining deliverable | Dependency |
| --- | --- | --- | --- |
| M0 | Reconciled | Maintain requirement/evidence map and reconcile newly found gaps | Start |
| M8a | Planned | Design baseline, shared components, shell and panel layout | M0 |
| M1 | Core implemented | Context inspector/opt-out, draft diff and exact-review boundary | M8a |
| M2 | Core implemented/verified | Polish bookmark workflow and complete regression acceptance | M8a; M1/M6 context changes |
| M3 | Adapters implemented; live tests partial | Provider acceptance matrix, supported-model controls and real account tests | Can proceed alongside other work |
| M4 | Core implemented | Resolution, title/caption, preview/estimate and full export acceptance | M8a |
| M5 | Recorder implemented; preservation limited | Observer coverage reconciliation and broader preservation evidence | M0; before M6 |
| M6 | Core implemented | Component inventories, affected-residue track and decision table | M5; M8a |
| M8b | Planned | Apply final visual system to every page and feature state | Functional increments |
| M7 | Partial | Final integration, installed-wheel, scientific and browser acceptance | All preceding gates |

Track each requirement as `planned`, `implemented`, `verified`, or
`blocked/deferred with reason`. A milestone becomes complete only when all its
required controls and acceptance checks are verified. A skipped test, synthetic
provider reply, unsupported platform or missing account is recorded explicitly.
Do not call the whole framework complete while a required gate remains open.

## M0 — Establish the baseline and keep scope controlled

1. Record the branch revision, current changed modules and the available completed
   study/fixtures. Classify each requirement as existing, missing or unverified.
2. Record scientific source hashes and current context, bookmark, clip and audit
   schemas. Use those baselines to detect accidental changes during redesign.
3. Identify real dashboard states: completed study, no study, missing analysis,
   unavailable playback, stale source, disconnected provider and failed request.
4. Separate provider availability from implemented application behavior. Record
   which backend/platform and installed client version each result covers.
5. Update the requirement map when an improvement changes scope. No requirement
   disappears merely because a simpler prototype exists.

**Exit:** a traceable list of requirements, implementation locations, acceptance
checks and unresolved dependencies; branch/base and scientific boundary confirmed.

## M8a — Define and build the visual foundation

### Design direction

Use a calm research workspace: neutral graphite surfaces, a restrained cyan
action accent, clear borders, readable type and generous alignment. Keep Graphite,
Ink and Paper themes available. Scientific warnings, qualifications and refusals
remain visually distinct and retain their text; color alone never conveys meaning.

Adopt these concrete design targets:

- Main reading text around 15–16 px; labels generally at least 13 px. Use one
  consistent sans-serif UI family and monospace for identifiers, code and units
  where useful. Avoid mixing serif chat text with unrelated UI typography.
- A shared spacing scale of 4/8/12/16/24/32 px, consistent control heights and
  corners, aligned form labels and predictable section spacing.
- Clearly separate page titles, section headings, primary actions and supporting
  evidence. Limit accent-filled buttons to the primary action in each task.
- Target contrast of at least 4.5:1 for ordinary text and 3:1 for large text and
  meaningful control boundaries. Disabled states remain understandable.
- Preserve black dropdown backgrounds and white option text across all themes,
  including selected, focused and disabled options. Inspect native open menus
  on Windows, as well as computed styles; the earlier contrast issue must not recur.
- Visible keyboard focus, descriptive icon labels, reduced-motion support and
  comfortable hit areas, aiming for 44 px on touch-facing controls.
- Offline-capable fonts/icons and styles. Reuse existing assets and small native
  elements; avoid a new framework or remote dependency for cosmetic work.

### Layout and components

1. Inventory existing tokens and contradictory CSS overrides. Consolidate the
   rules for buttons, fields, sliders, chips, alerts, cards, dialogs and drawers.
2. Refine the study/navigation sidebar so identity, progress and navigation have
   clear hierarchy. Keep current labels/routes unless a documented usability
   improvement justifies a change.
3. Place Agent and bookmark entry controls in a predictable toolbar. Remove the
   floating research controls' overlap with the composer and page actions.
4. On wide screens, allow the Agent panel to use space beside the research view.
   On smaller screens, use one accessible drawer. Opening bookmarks must not
   pile an additional panel over the Agent or hide required task controls.
5. Make dialogs share headers, sections, validation placement and action footers.
   Keep close/cancel and primary actions reachable when content scrolls.
6. Preserve user camera/frame/selection when changing panel layout. Resizing the
   viewer redraws the scene without resetting it or changing its data.

`theme.css` is shared with self-contained generated reports. Scope dashboard-only
rules to dashboard components and review every shared-token change for its report
effect. Existing saved reports and figures are never rewritten by the redesign.

**Exit:** representative Overview, Agent, Viewer and Settings layouts use the
shared system; no composer/control overlap at the target widths; both the live
dashboard and a generated report sample retain usable text and layout.

## M1 — Finish context transparency and draft review

1. Add a compact context summary beside the composer and an expandable inspector
   showing the page, graph/range, warning, selected identity, source frame/time,
   preparation event and setting where present. Display evidence references and
   explicitly identify unavailable fields.
2. Add a per-message “Use current view” control. Turning it off excludes selected
   view evidence while retaining the study identity needed for request isolation.
   Specify how prior conversation context behaves; do not imply that the toggle
   erases information already sent in earlier messages. Offer a fresh conversation
   when the user needs an explanation independent of earlier view context.
3. Freeze the outbound context at Send. Resolve/verify evidence server-side and
   reject stale replies after study/account/model changes. The inspector must
   reflect the same request context, not merely a newer screen selection.
4. Show a before/after draft diff: changed fields, existing/requested values,
   units, validation results and scientific implications. Distinguish an absent
   value from a default/resolved value; do not silently fill scientific choices.
5. Keep Add to draft separate from save/apply/run. Tie review to the precise
   canonical draft and study revision; editing either invalidates prior review.
   Reuse human builder controls and validators rather than adding an AI execution
   endpoint or a broad approval that authorizes future changes.
6. Update packaged Markdown for context controls, draft behavior and evidence
   limits. Preserve all registered error coverage.

**Exit:** all eight context types can be inspected; opt-out truly changes the
request evidence; changed drafts/studies require fresh review; adversarial model
output cannot execute or apply settings; disabled Agent starts no new requests.

## M2 — Finish the bookmark experience and acceptance

1. Use a consistent drawer with title, screenshot/placeholder, tags, note, date
   and source/restore status. Put search/filter and import/export where users can
   find them without competing with molecular controls.
2. Make capture scope visible before saving. Retain optional screenshots and
   explicit stale-source notices, and keep chat/authentication outside captures.
3. Keep contextual save actions for graphs, report figures, viewer frames,
   relevant settings and preparation events. Avoid actions on empty/login views.
4. Verify updated context and audit visuals round-trip through bookmarks, including
   camera, graph range, pinned residue, stage pair and display-only overlay.
5. Recheck restart persistence, edit/delete conflicts, duplicate IDs, bundle size
   and paths, import preview/collision choices, moved studies and missing sources.

**Exit:** end-to-end capture → restart → find → restore → export → import works;
stale records remain useful without selecting unrelated data; no credentials or
scientific datasets are accidentally included in portable bundles.

## M3 — Finish provider/model acceptance

1. Give OAuth connections a clear provider dropdown, one contextual Connect action
   and status area: disconnected, connecting, connected, expired, unavailable or
   failed. State official-client requirements where relevant.
2. Display actual account/model availability and preserve the requested Codex
   ordering. Refresh supported model/reasoning information from official sources
   and the native account catalog when implementation resumes; account access
   takes precedence over a marketing model list.
3. Show the selected reasoning level beside its slider, supported endpoints and
   a concise speed/depth explanation. Models with fixed or unsupported effort
   expose that state rather than a fictitious adjustable range.
4. Complete real Claude, Kimi and Gemini acceptance with user-performed normal
   login: enumeration, harmless explanation, cancellation, expiry/refresh,
   account switching and disconnect. Never copy another client's credentials,
   silently select API billing, or infer entitlement from successful login.
5. Recheck zero write/shell/tools, credential isolation, rejected/stale requests
   and error visibility. Preserve completed OpenAI acceptance as a regression case.

**Exit:** a provider-by-provider matrix names route, client version, credential
owner, returned models, supported reasoning controls and live-test outcome.
Missing user accounts/consent remain open dependencies; unrelated milestones
continue. Full multi-provider completion requires those real tests.

## M4 — Complete the export dialog and rendering contract

1. Organize the dialog into Frames, Camera, Labels and Output, with a preview and
   fixed action area. Retain MP4, GIF and Both and current range/stride/fps controls.
2. Add bounded resolution presets and clearly show resulting dimensions/aspect
   ratio. Re-render at the selected supported size rather than presenting an
   enlarged low-resolution screenshot as improved quality. Bound rendering memory,
   upload size and frame counts on both client and server.
3. Add independent title/study-name and custom-caption checkboxes/fields alongside
   residue, atom, frame and physical-time labels. Bound text length, keep overlays
   legible and show crowding limits. Labels follow verified identities per frame.
4. Preview the selected first frame and representative rotation. Show browser
   range, exact source-frame/time equivalents, number of frames and playback
   duration. Mark media-size/runtime estimates as approximate; unknown physical
   time remains unknown. Preview uses the same rendering choices as export.
5. Record dimensions, captions and all options in the provenance sidecar. Maintain
   encoder detection, source freeze, unique paths, progress and viewer restoration.

**Exit:** MP4/GIF decode at selected dimensions; frame order/time/labels match the
preview and source; rotation is presentation-only; cancel, invalid input, missing
encoder and failure restore the viewer; downloads match saved artifacts and hashes.

## M5 — Close provenance gaps and preservation evidence

1. Map the planned operations to actual current recorder events. Distinguish
   requested versus resolved choices and recorded operations versus observations.
   Identify which stages are aggregate and what evidence is unavailable.
2. Where an existing scientific operation exposes a result/decision, add bounded
   observation at that point. Do not run it again, consume randomness, modify
   scientific objects, or fabricate fine-grained solvent/ion events from totals.
3. For details unavailable from the backend, retain explicit aggregate/incomplete
   status. Such a limitation must be documented against the original requirement;
   do not label unavailable operation-level recording complete without an explicit
   scope decision. Full per-atom snapshots are unnecessary where bounded mappings
   and trustworthy counts suffice.
4. Preserve versioned records, immutable source snapshots and checksums; test
   limit/error handling and compatibility with historical records.
5. Extend audit-on/off checks using small seeded protein and protein/ligand
   fixtures, and a membrane fixture where the installed backend supports it.
   Compare atom identities/order, topology, System parameters/constraints,
   positions/box and relevant outputs on the same backend/platform. Document
   nondeterminism and justified tolerances instead of forcing numerical equality.
6. Check recorder overhead/storage on representative bounded fixtures. Scientific
   execution must retain its existing failure and refusal behavior when audit
   storage is unavailable; recording failures remain visible.

**Exit:** coverage map reconciled; newly recorded events match operations; historical
limitations are honest; successful equivalence checks name exact backend/fixture
coverage, and unsupported preservation gates remain explicitly open.

## M6 — Complete scientific transparency visuals

1. Preserve the collapsed default and reusable source → selected → repaired →
   prepared stage presentation. Do not make the audit the main dashboard task.
2. Separate protein, confirmed ligand, water, ion and unknown/other inventory
   series with labels/legend. Do not identify a component as a ligand from a
   residue name alone. Distinguish atom counts from residue/molecule counts.
3. Add a chain-aware affected-residue track for recorded repairs, mutations,
   removals and state changes. Preserve insertion codes and ambiguous assembly
   copies; missing mappings are listed rather than assigned to a false position.
4. Add a decision table with choice, requested/resolved value, recorded reason,
   affected component/stage, source and evidence status. Include assembly,
   heterogen policy, protonation/pH, force-field XMLs and ligand parameterization
   only where supplied. Keep raw event detail expandable for traceability.
5. Link bars, track markers and table rows to verified stages/selections and
   before/after evidence. Unavailable correspondence explains the limitation
   instead of highlighting a different atom.
6. Preserve exact-correspondence overlay guards and display-only alignment labels.
   Agent questions receive server-verified event IDs; the same factual summary
   works without a model. Bookmark restoration covers the new selection state.

**Exit:** controlled fixtures show correct inventory/category/decision information;
clicks resolve exact identities; ambiguity refuses misleading alignment; historical
and future studies clearly separate recorded causes from observed differences.

## M8b — Apply the aesthetics revamp across the dashboard

| Surface | Planned improvements | Review condition |
| --- | --- | --- |
| Studies and Overview | Clear study identity, status/progress hierarchy, useful empty states; secondary audit disclosure | Important results/warnings are discoverable without crowding |
| Config/builder | Aligned sections, units/help next to fields, readable validation and draft diff | Human scientific choices remain explicit |
| Analysis | Consistent graph cards, legends/units, range controls, selection summary and contextual actions | No obscured axes, changed data/scale or broken residue mapping |
| Molecular viewer | Grouped view/playback/selection/export tools; clear pinned/selected identities | Scene stays usable with panels open; state survives resizing |
| Agent | Readable transcript, source references, inspectable context, uncrowded composer and reasoning controls | Navigation/context/sending behavior remain clear |
| Bookmarks | Thumbnail/list hierarchy, tag/search controls, restore status and import preview | Works with many records and absent screenshots |
| Preparation audit | Consistent stage cards, readable charts/table, selected-event emphasis | Optional panel remains secondary; evidence status is visible |
| Report and Files | Clear figure/file actions and readable evidence links | Scientific content/export behavior is preserved |
| Settings/connections | Group appearance, Agent preference and provider controls; clear progress/errors | Black/white dropdowns and account/model choices stay legible |

Review every supported theme at 1440, 1280, 1024, 768 and 390 px widths, plus
200% text zoom. Charts/tables may have a labelled local scroll area; ordinary
page controls must not require horizontal scrolling. Check modal focus/return,
Escape handling, keyboard navigation, loading/success/error/empty/stale states,
long titles, long warnings and unavailable screenshots/models. Use before/after
captures of representative pages and complete actual interaction checks; a static
mockup alone does not satisfy this milestone.

**Exit:** each surface/state passes the visual checklist, native dropdowns are
readable, accessibility and responsive interactions pass, and no scientific
values, labels, plot definitions or selected identities changed for aesthetics.

## M7 — Final integrated acceptance and GitHub handoff

1. Run relevant existing scientific/dashboard suites and the new behavior tests
   on the completed scope. Report failures/skips with causes; retain provider
   synthetic tests separately from live account outcomes.
2. Exercise one integrated completed-study flow: inspect/select a graph residue →
   pin/compare → explain with correct context → bookmark → restart/import/restore
   → export both media formats → inspect/restore an audit event. Verify source
   checksums and context isolation throughout, without a production simulation.
3. Verify human review with changed drafts and studies, provider/account switches,
   disabled sidebar, missing evidence, disconnected network and stale replies.
4. Build the wheel from the final feature revision and test it outside the source
   checkout. Verify Markdown/error resources, static assets/provider bridges and
   required extras; record artifact hash and revision. Source-tree tests alone
   are insufficient packaging evidence.
5. Review all scientific instrumentation and UI changes in the branch diff.
   Reconcile each original requirement with a passing check or an explicit open
   limitation. Existing preservation receipts do not certify newly added code.
6. Update README/changelog, model/knowledge docs, the original framework and status
   table. Publish milestone commits to the user's branch with concrete behavior,
   test scope and limitations. Prepare a coherent upstream PR description when
   requested; no merge or upstream integration is part of this plan.

**Exit:** M0–M8 requirements and gates all have current evidence; any unresolved
provider/backend dependency prevents an unqualified whole-framework completion
claim. The branch is clean, remote matches the reviewed commit, documentation
describes the final behavior and remaining limitations honestly.

## Working rules and obvious improvements

- Implement design foundation first, then functional gaps, then final visual
  polish and acceptance. Avoid polishing a dialog before its required controls
  and data contract are defined.
- Reuse a context/evidence component for Agent, bookmarks and audit, a shared
  dialog layout, and one set of loading/empty/error patterns. Keep fixes small
  enough to review without introducing parallel scientific paths.
- Update context/bookmark/clip/audit schemas compatibly; old records remain
  readable. New fields need bounded validation and safe missing-field behavior.
- Treat warning legibility, source provenance, units and uncertainty as design
  requirements. Do not hide them to make a page look cleaner.
- Fix clear overlap, inconsistent spacing, unreadable text and keyboard issues
  within the authorized scope. Document consequential navigation, scientific or
  provider scope changes before treating them as accepted requirements.
- After each increment: inspect → implement → verify the relevant success and
  failure behavior → review diff → update status/docs → commit/push. Broaden
  tests only when changed scope or evidence warrants it; M7 remains the final
  integrated gate, and no milestone stop is reintroduced.
