> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# Building Topology Contract

## Purpose

Turn the verified `source-index` into one shared architectural topology before
facade detailing. Treat the topology white model as the structurally correct
stage of the final SKP, not a disposable massing study.

This stage owns architectural interpretation. The later SketchUp builder owns
execution and must not silently reinterpret CAD.

## Agent boundary

The topology derivation agent may:

- register all confirmed plan, roof-plan, elevation, and section views;
- derive levels, floor envelopes, ordered exterior-wall rings, slabs, roofs,
  openings, curtain-wall envelopes, molding paths/profiles, and canopies;
- create or update the topology white-model stage;
- emit a topology candidate and review artifacts.

It must not:

- mark its own candidate verified;
- omit a conflicting view to simplify the model;
- replace measurable geometry with a visual guess or default;
- add final frames, materials, decorative subdivisions, or unrelated detail;
- call the topology white model final or delivered.

The independent topology reviewer must use a different derivation ID and must
not inherit the topology agent's hidden reasoning. Give the reviewer the
verified source index, topology candidate, white-model artifact, and raw
same-view review images.

## A2A artifacts

Use these files as the only authoritative stage handoff:

```text
work/contracts/source-index.json
work/topology/topology-derivation.json
work/topology/topology-plan.json
work/topology/white-model-result.json
work/contracts/building-topology-candidate.json
work/topology/topology-white-model.skp
work/topology/review-views/<source-view-id>.jpg
work/contracts/topology-review.json
work/contracts/building-topology.json
```

Bind every file by SHA-256. Do not pass architectural decisions only through
chat summaries.

## Derivation sequence

### 2.1 Register every model-driving view

Normalize the project coordinate system to millimetres. Record one transform
for every included plan, roof plan, elevation, and section in `source-index`.
Bind each registration to its `source_view_id`, unique `qa_view`, drawing role,
source-CAD origin, the exact topology IDs visible in that view, and at least
two asymmetric CAD anchors. `source_origin` is the source-view coordinate that
maps to the registration's 3D `origin`; it is required for Step 4 reverse
projection. `visible_topology_ids` must contain only real topology IDs and must
exclude floors or facade systems that are not visible in that source view.

Block when a required source view is unregistered, duplicated, assigned a
different role, or registered from title text alone.

Create the derivation template directly from the verified first-stage handoff:

```powershell
python scripts/create_topology_derivation.py `
  --project <project> `
  --source-index work/contracts/source-index.json `
  --out work/topology/topology-derivation.json
```

The generated registrations are deliberately `candidate`, not verified. The
topology derivation agent must fill each transform, outward/view direction,
section cut plane where applicable, and at least two asymmetric anchors. It
may set a registration to `verified` only after those fields agree with the
source view. Clear `blocking_reasons` only from measured evidence.

### 2.2 Derive the global envelope topology

Derive each level's true footprint from the registered views. Include local
projections, setbacks, first-floor ears, entrance recesses, and roof changes.
Define every exterior wall as an ordered segment with thickness and evidence.
Require the ordered segments to form one closed ring and one future SketchUp
group per level.

When an elevation or section shows that a wall meets a pitched, stepped, or
otherwise non-horizontal roof underside, record a full-length `top_profile` as
ordered `[station_mm, elevation_mm]` points measured along the directed wall.
The profile must start at station 0, end at the wall length, cite elevation or
section evidence, and close the envelope against the confirmed roof underside.
A uniform level-top wall is forbidden when it would expose the interior below
the roof.

Make the exterior wall ring the owner of the exterior face. A plan wall that
duplicates the same face is context, not another modeled wall.

An interior wall that terminates at the exterior envelope must stop at the
cited exterior wall's inner face. Record the endpoint rule and host wall ID;
never extend an interior-wall centerline to the exterior face, because its end
face creates an unsupported facade seam. After splitting walls around true
openings, erase only coplanar construction seams. Preserve real jamb, head,
sill, silhouette, corner, and material-boundary edges.

Keep every slab inside or exactly on its owning envelope. Record roof boundary,
base level, topology type, and source evidence.

Represent every roof skylight strip, hatch, or opening as a `roof_light` with
its host roof ID, measured 3D boundary on the roof plane, system type,
true-opening state, and cross-view evidence. Never flatten a pitched-roof
skylight to XY or disguise it as a wall opening or curtain wall.

### 2.3 Derive hosted and cross-view topology

For each ordinary opening, record its level, host-wall ID, plan position, sill,
width, height, cut depth, and both plan and elevation evidence. Use measured
wall thickness as cut depth. Use 200 mm only when the wall thickness is absent,
and record `default_200_missing_wall_thickness`.

Represent curtain walls separately as registered 3D facade envelopes. Do not
treat curtain-wall panels as ordinary wall cutters.

Represent every molding or cornice as:

```text
continuous 3D path + complete 2D profile + vertical datum
+ covered facades + corner policy + termination policy + source evidence
```

A path crossing a corner requires evidence from the adjacent facade views.
Do not infer a profile from color, lineweight, or a single uninterrupted line.

Represent a canopy as a hosted closed volume with a plan footprint, base
elevation, measured thickness, edge form, and evidence from at least two
elevation views. A plan projection may support depth and boundary evidence.

### 2.4 Produce the topology white model

Use the same SKP that will continue into final modeling. Make the following
reviewable without final decorative detail:

- every true floor outline and level change;
- one continuous exterior-wall group per level;
- slab and roof limits;
- true ordinary openings and curtain-wall envelopes;
- molding paths, profiles, corner turns, and terminations;
- complete canopy volumes and facade attachment;
- no duplicate exterior faces or visible debug geometry.

Export one same-orientation review image for every registered source view.
Bind each image to its `source_view_id` and hash.

Compile the completed derivation. This command performs strict JSON Schema and
semantic checks; failure means SketchUp must not be changed:

```powershell
python scripts/compile_topology_plan.py `
  --project <project> `
  --derivation work/topology/topology-derivation.json `
  --out work/topology/topology-plan.json
```

Add the plan path and hash to `workflow-state.json`, confirm the exact open
SketchUp target, and run the dedicated pre-confirmation gate:

```powershell
python scripts/workflow_gate.py --project <project> --stage topology
```

Only after `PASS`, load `scripts/build_topology_white_model.rb` through the
SketchUp bridge and invoke:

```ruby
HEBITopologyWhiteModel.run('C:/project/work/topology/topology-plan.json')
```

The builder is the single writer for `HEBI_TOPOLOGY_WHITE_MODEL`. Re-running
it replaces only that named group, creates one wall-ring group per level,
cuts ordinary openings as real voids, builds the other topology systems, saves
the SKP, exports every registered review view, and writes the hash-bound result.
It does not authorize facade-detail production.

Convert that real SketchUp result into the reviewable topology candidate:

```powershell
python scripts/finalize_topology_candidate.py `
  --project <project> `
  --plan work/topology/topology-plan.json `
  --white-model-result work/topology/white-model-result.json `
  --out work/contracts/building-topology-candidate.json
```

Never create the candidate before the SKP and all review JPGs exist. A stale
plan, model, or review hash blocks finalization.

### 2.5 Run independent topology review

Create a review template:

```powershell
python scripts/audit_building_topology.py `
  --project <project> `
  --candidate work/contracts/building-topology-candidate.json `
  --review-template work/contracts/topology-review-template.json
```

Follow [review.md](review.md) to prepare the packet and preserve this template.
The human or independent agent completes a separate `topology-review.json`.
Have a different visual derivation inspect every registered view. It must check
registration, silhouette completeness, host relationships, and cross-view
consistency. It must also check the complete building system: floor footprints,
wall rings, slab containment, openings, curtain walls, sweeps, canopies, roofs,
unsupported additions, and white-model readability.

Do not convert `PENDING` or uncertainty to `PASS`. Record the discrepancy in
`unresolved`, return it to topology derivation, regenerate all affected review
views, and create a new candidate hash.

### 2.6 Bind user confirmation and promote

Show the reviewed topology white model and its per-view evidence. Read
`topology-confirmation-boundary.md`; present its mandatory review boundary,
allowed-to-ignore boundary, and the completed source/white-model system
inventory. Do not rely on the user to remember these rules. A source-present
item marked absent in the white model blocks confirmation. Record the user's
explicit confirmation ID and instruction against both the white-model hash and
candidate hash. Record the actual review/confirmation response with
`review_session.py record` after these fields are complete; then promote:

```powershell
python scripts/audit_building_topology.py `
  --project <project> `
  --candidate work/contracts/building-topology-candidate.json `
  --review work/contracts/topology-review.json `
  --out work/contracts/building-topology.json
```

Promotion must fail when the reviewer shares the topology derivation ID, an
artifact hash is stale, any registered view lacks a passing result, any system
check is not true, user confirmation is absent, or final semantic validation
finds a geometry defect.

## Repair routing

Classify every failure by its earliest source:

- missing or clipped CAD evidence: invalidate `source-index`;
- wrong registration, envelope, host, path, profile, or cross-view relation:
  invalidate the topology candidate and every downstream contract;
- correct topology but incorrect SketchUp geometry: retain the confirmed
  topology and invalidate `sketchup-build` and QA;
- changed white-model SKP or review image: invalidate topology review and user
  confirmation because their hashes are stale.

Never repair a topology error only inside the final SketchUp geometry. Correct
the topology contract first so the same failure cannot silently recur.

## Legacy extraction boundary

`extract_source_geometry.py`, `build_model_intent.py`, and the older reviewed
build-plan chain may supply measurements or debugging evidence. They are not
the topology authority and must never drive SketchUp directly. Only a verified
`topology-plan.json` may generate the white model; only a promoted
`building-topology.json` may drive detailed production modeling.
