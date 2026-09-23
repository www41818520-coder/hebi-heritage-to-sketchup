> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# SketchUp Production Build Contract

## Purpose

Turn a confirmed `building-topology` contract into one traceable production
SKP without reinterpreting CAD inside Ruby. This stage owns deterministic
execution, reusable component placement, material assignment, and model-internal
self-checks. Independent plan/elevation/section agreement remains Step 4.

## A2A artifacts

```text
work/contracts/building-topology.json
work/build/sketchup-build-derivation.json
work/build/sketchup-production-plan.json
work/build/sketchup-production-result.json
reports/build/sketchup-production-self-check.json
work/contracts/sketchup-build.json
output/<project>-production-<timestamp>.skp
```

Bind every handoff by SHA-256. Do not pass production dimensions or target
model identity only through chat.

## 3.1 Resolve production-only details

```powershell
python scripts/create_sketchup_build_derivation.py `
  --project <project> `
  --topology work/contracts/building-topology.json `
  --output-model output/<project>-production-<timestamp>.skp `
  --out work/build/sketchup-build-derivation.json
```

The generated file blocks each unresolved opening assembly, curtain-wall
system, and material assignment. Resolve these from the cited source views:

- opening: family ID, frame width/depth, wall inset, panel type, mullion and
  transom ratios;
- curtain wall: family ID, frame width/depth, inset, panel type, horizontal
  and vertical grid ratios;
- material: annotation meaning, RGBA display value, and exact topology IDs.

Use `void_only` only when confirmed scope or CAD evidence explicitly calls for
an unfilled opening. Never infer material from CAD display color alone.
Repeated openings may share a family ID only when type, size, frame, panel,
and subdivision signature are identical.

Set `status: ready_for_compile`, give the derivation a unique ID, and clear
blockers only after every record is `verified` with evidence.

## 3.2 Compile and authorize

### Simple CAD-annotated materials

Simple material assignment is included by default when CAD notes, schedules,
or keyed legends identify finishes. Keep the original material name and source
view/text reference, map the intended surface region to exact topology IDs,
and populate the existing material assignments. Reuse materials with the same
finish specification across targets.

- Explicit color and type (for example dark-gray metal panel): create a named
  flat-color SU material. A verbal color permits an approximate display swatch;
  record that approximation, not a fabricated manufacturer color code.
- Type only (stone, concrete, metal, wood): retain the specified type in the
  material name and use a neutral illustrative swatch, identified as a display
  assumption. Do not claim that the color was specified in CAD.
- Glass: use simple transparency when the source supports glass; record the
  display opacity as illustrative unless a value is specified.
- No finish evidence: retain a neutral unassigned/default appearance.

Do not fetch textures or add render-engine materials for this simple scope.
CAD entity colors or hatch patterns need a keyed legend before they establish
a building finish. Material labels alone never imply extra geometric relief.
Separate distinct finish zones before assignment; do not paint an entire host
group when only one facade strip is annotated. If the generator cannot address
the region, represent the surface zone first rather than claiming assignment.

Check the actual model's visible material and coverage, including child-face
overrides of a group material. Show the material names, source, targets and
approximate swatches in the normal production/acceptance summary. Do not add
a separate user confirmation for ordinary illustrative color choices. Ask only
when conflicting labels or ambiguous zone boundaries materially change the model.


```powershell
python scripts/compile_sketchup_build_plan.py `
  --project <project> `
  --derivation work/build/sketchup-build-derivation.json `
  --out work/build/sketchup-production-plan.json
```

Compilation proves current topology/white-model hashes, exact topology-ID
coverage, source-proven production details, consistent component families,
and project-contained distinct output paths.

Record the plan path/hash as `production_plan` in workflow state, confirm the
exact user-opened white model, then run:

```powershell
python scripts/workflow_gate.py --project <project> --stage build
```

## 3.3 Execute in SketchUp

Load `scripts/build_sketchup_production_model.rb` through SketchUp MCP or the
local bridge and invoke:

```ruby
HEBIProductionBuild.run('C:/project/work/build/sketchup-production-plan.json')
```

The generator must reject a different or unsaved active model; replace only
its authorized white/production roots; preserve the original white-model file;
save to a new output; make one mitered wall-ring group per level; split real
openings; reuse door/window component families; build measured roofs, slabs,
curtain walls, sweeps, and canopies; apply only traceable materials; and emit
persistent IDs, counts, bounds, failures, and geometry checks. Test each
opening's host-wall penetration line independently from its frame/panel group.

A completed Ruby call is not proof of success. Missing IDs, failed solids,
duplicate faces, incomplete systems, stale targets, or unresolved items block.

## 3.4 Finalize the build handoff

```powershell
python scripts/finalize_sketchup_build.py `
  --project <project> `
  --plan work/build/sketchup-production-plan.json `
  --result work/build/sketchup-production-result.json `
  --out work/contracts/sketchup-build.json

python scripts/workflow_gate.py --project <project> --stage qa
```

Finalization independently checks artifact hashes, exact topology coverage,
persistent IDs, zero failed solids, statistics, and every geometry check. The
`qa` gate permits Step 4 independent comparison; it does not declare delivery.

## Repair routing

- Missing production detail in CAD reading: repair `source-index` evidence.
- Wrong footprint, host, level, sweep basis, or canopy topology: return to
  Step 2 and invalidate every downstream artifact.
- Correct plan but wrong generated entity: repair the production generator and
  regenerate the output SKP and `sketchup-build` contract.
- User-visible view mismatch: route from independent QA to the earliest
  responsible contract; never patch only the SKP.
