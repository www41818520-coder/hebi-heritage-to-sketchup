> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# Drawing Input Requirements

Use this reference before preflight or frame indexing.

## Required intake

- Approved CAD source folder or files.
- DWG or DXF source; require a matching ASCII DXF for structured extraction.
- Unit basis from the CAD header or the user.
- Requested modeling scope and delivery level.
- Optional basic control mass or closed CAD control polylines.

Do not require one drawing type per CAD file. A single model-space file may
contain multiple plans, elevations, sections, schedules, legends, details,
and title frames.

## Full-deliverable evidence

- Complete plan evidence for every modeled floor and roof.
- Complete elevations for every modeled side.
- Sections or measurable annotations for levels, roof/parapet, ground line,
  indoor/outdoor datums, and vertical transitions.
- Readable dimensions, level marks, titles, opening symbols, and terminal axis
  bubbles where present.
- Door/window schedules or readable block semantics when they drive detail.
- Material legends, hatch notes, or facade annotations when appearance matters.
- Explicit side naming or orientation evidence.

If this evidence is absent, use `candidate` or stop. Never promise full
delivery from a package that cannot support its final QA views.

## Export quality

- Preserve layouts, title frames, title-block inserts, attributes, dimensions,
  repeated view titles, and nested blocks.
- Keep true CAD coordinates and aspect ratio.
- Do not crop away titles, axes, levels, or drawing context.
- Remove remote junk only when it is demonstrably outside all project frames.
- Use PDF/JPG as visual support, never as a substitute for source geometry.

## Insufficient inputs

Identify the exact missing role or evidence. Then either:

1. use a better DWG/DXF export;
2. repair reader classification or block expansion when CAD already contains
   the evidence;
3. downgrade to `candidate` for a non-critical gap;
4. stop before topology confirmation.

Do not silently infer a frame, facade direction, height system, or model scope
from visual similarity alone.
