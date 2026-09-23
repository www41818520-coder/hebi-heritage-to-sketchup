> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# 3. Execute the SketchUp build contract

Confirm the exact user-opened SketchUp target. Read
[references/sketchup-build-contract.md](sketchup-build-contract.md)
for production specification, compilation, generation, self-check, and
finalization. Read
[references/sketchup-mcp-integration.md](sketchup-mcp-integration.md)
for MCP control or
[references/sketchup-execution.md](sketchup-execution.md) for the
local bridge.

Create `sketchup-build-derivation.json` from confirmed topology. Resolve every
opening family, curtain-wall grid, and material target from cited CAD evidence;
do not let Ruby invent production details. Compile the hash-bound production
plan, record it in workflow state, and run the `build` gate.

Build idempotent versioned groups, tags, and reusable components. Every mutation
must start a SketchUp operation, commit only on success, abort on error, and
save a new timestamped SKP under `output/`.

Use this order:

1. levels, true floor footprints, and continuous exterior wall rings;
2. contained slabs, roof, and primary local masses;
3. true wall cuts and reusable door/window or curtain-wall systems;
4. continuous molding/cornice sweeps with corner turns and terminations;
5. canopies and other cross-view local topology;
6. material surfaces and non-geometric appearance;
7. cleanup of duplicate faces, cutters, guides, and debug geometry.

Record one result for every required topology ID. A script exit code is not a
result. Finalize the actual output with `finalize_sketchup_build.py`. The
`sketchup-build` contract passes only when SKP/report hashes, persistent entity
IDs, exact coverage, zero failed solids, statistics, and geometry checks pass.

Before independent QA, run:

```powershell
python scripts/workflow_gate.py --project <project> --stage qa
```
