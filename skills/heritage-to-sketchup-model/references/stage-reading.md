> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# 1. Bind and verify the source index

Apply [review.md](review.md). A human or another agent can independently review
the evidence. Record the actual response; no second agent is mandatory.

Accept a confirmed project folder containing:

```text
input/cad/
input/references/
requirements/
```

Require DWG or DXF as the source and an ASCII DXF for structured extraction.
Treat a single CAD file containing multiple plans, elevations, sections,
details, and frames as normal. PDF/JPG may support visual review but cannot
replace source geometry.

Read [references/drawing-input-requirements.md](drawing-input-requirements.md)
and [references/cad-reader-contract.md](cad-reader-contract.md).
Then:

1. fingerprint every approved source;
2. determine units from the CAD header or the user;
3. find complete title frames from layouts, title blocks, or closed frame
   polylines, never fixed bands or guessed equal splits;
4. create one readable, true-aspect-ratio JPG and one vector master per frame;
5. inventory every nested plan, roof plan, elevation, section, detail, and
   schedule with stable frame/view IDs; for mixed frames, partition views from
   title anchors, CAD geometry clusters, and whitespace valleys, and record
   separator evidence and confidence;
6. join same-baseline CAD title fragments into canonical titles while retaining
   every source text ID; derive a unique QA key and axis/cardinal/section
   direction key from the canonical title;
7. index terminal axis labels, level datums, scales, dimensions, annotation
   counts, and their source Handles for every model-driving view;
8. recursively expand blocks, attributes, dimensions, text, and opening
   subdivisions;
9. render through the `ezdxf` Recorder so every visible top-level Handle maps
   to actual drawing primitives;
10. write one render manifest and trace-bounds SVG per frame/view, including
   recursive block/dimension lineage, missing Handles, fallback-rendered text,
   invisible skips, and crop-edge contacts;
11. compare visible source entities with rendered entities and require 100%
    vector coverage before visual readability review;
12. have an independent visual derivation inspect every complete frame and
    nested view, explain exact crop-edge Handles, and promote the package to
    `verified` only through `audit_cad_reader_package.py`.

Default to a PDF vector master per complete frame. Derive nested-view manifests
from the owning frame render; generate per-view detail JPGs only when small
annotations need magnification. When an unavailable SHX/bigfont causes visible
Chinese text to disappear, render it with the Unicode fallback and record the
fallback method and font in the manifest. Never count an explicitly invisible
entity or hidden-layer entity as a missing render.

Block `source-index` when a frame or model-driving view is clipped, incomplete,
unreadable, missing source entities, missing a stable QA key, or assigned an
unresolved drawing role. Also block when coverage is below 100%, any visible
Handle lacks render primitives, any evidence hash is stale, or crop-edge
contacts remain unexplained. For mixed frames, also block low-confidence
segmentation, protected geometry crossing a separator, or any title-only grid
fallback. Run:

```powershell
python scripts/create_frame_confirmation_pages.py `
  --input <approved.dxf> `
  --work-dir work/reading

python scripts/audit_cad_reader_package.py `
  --project <project> `
  --package work/reading/region_candidates.json `
  --review-template work/reading/independent-review-template.json

# Follow review.md: prepare packet, review a COPY, record response/receipt.
# Preserve the template; completed output is independent-review.json.
python scripts/audit_cad_reader_package.py `
  --project <project> `
  --package work/reading/region_candidates.json `
  --review work/reading/independent-review.json `
  --out work/reading/verified-reader-package.json

python scripts/create_source_index.py `
  --project <project> `
  --project-id <project-id> `
  --source <approved.dxf> `
  --regions work/reading/verified-reader-package.json `
  --required-roles plan,elevation,section `
  --out work/contracts/source-index.json

python scripts/workflow_gate.py --project <project> --stage reading
```
