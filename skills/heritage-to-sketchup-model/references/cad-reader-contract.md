> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# CAD Reader Contract 2026-08-05

Use this contract whenever one DWG/DXF contains multiple architectural
drawings.

## Evidence hierarchy

```text
CAD source
  -> layout/model space
    -> complete drawing frame
      -> nested drawing view
        -> traceable source entities
```

Never assume one file equals one floor, sheet, or drawing type. Prefer a DWG
plus matching ASCII DXF; retain DWG as the original and use DXF for structured
reading. PDF/JPG is supporting visual evidence only.

## Frame detection

Try in order and record the selected method:

1. non-empty paper-space layouts;
2. title-block inserts and attributes;
3. closed frame polylines on title/border layers;
4. rectangles assembled from frame-layer lines;
5. full layout extents as a blocked fallback.

Never split by fixed coordinates, equal-width bands, title insertion points,
or image dimensions alone.

## View inventory

Within every complete frame:

1. inventory every plan, roof plan, elevation, section, detail, and schedule;
2. preserve repeated titles at different coordinates as separate views;
3. label frames containing multiple drawing roles as `mixed`;
4. assign every model-driving view a stable frame ID, view ID, role, bounds,
   QA key, and intersecting CAD entity IDs;
5. keep the complete frame page as the authoritative review artifact.

For two or more model-driving views in one frame, calculate candidate separators
from all three evidence classes:

1. title anchors establish probable ownership and reading order;
2. non-text CAD entity bounds establish distinct geometry clusters;
3. low-density whitespace valleys establish separators that avoid protected
   outlines, openings, and section geometry.

Search separators between adjacent title anchors, minimize protected-geometry
crossings, and record assigned entity IDs, separator coordinates, crossing
Handles, method, and confidence. Treat dimensions, long axes, and shared ground
lines as low-weight context so one guide cannot merge two drawings. Preserve a
title-only grid only as `title_anchor_grid_fallback`; mark it low-confidence and
blocked. Repeated titles remain distinct when their anchors and geometry clusters
are distinct.

A detail crop may help read small annotations but never replaces the complete
frame.

## Rendering and coverage

Render every frame as one high-resolution true-aspect-ratio JPG plus a vector
master. Default the vector master to PDF; use SVG only when downstream tooling
requires it. Recursively expand nested blocks and dimensions. Preserve dimensions,
text, attributes, level marks, opening labels, terminal axis bubbles, complete
outlines, and block geometry when present in CAD.

Record:

- source entity census by type;
- nested view inventory;
- source entity IDs per view;
- render engine and frame-detection method;
- missing entity classes and coverage status.

Use one `ezdxf` Recorder pass as the source for the frame JPG/PDF and render
manifest. For every visible top-level Handle, record primitive count and render
bounds. Record recursively expanded block/dimension descendants with lineage,
but bind their rendered primitives to the owning top-level Handle.

Classify entities as:

- `rendered`: Recorder produced primitives;
- `fallback_rendered`: unavailable SHX/bigfont suppressed visible text, then a
  Unicode fallback restored it to the same JPG/PDF;
- `skipped_not_visible`: entity is explicitly invisible, on a hidden layer, or
  empty text;
- `missing`: a visible source Handle produced no primary or fallback primitive.

Require coverage ratio `1.0` over expected-visible entities. Any `missing`
Handle blocks the source index. Fingerprint the JPG, vector master, manifest,
and trace SVG in the source-index contract.

Render each complete frame once. Derive nested-view manifests by spatially
slicing the owning frame manifest so all views use the exact same render
evidence. Generate optional per-view detail JPGs only for magnification; do not
export redundant per-view vector masters by default.

List non-frame entities touching frame or view bounds. Mark every contact as
explained or leave it unresolved. An unexplained roof edge, wall outline, axis,
dimension, or opening line at a crop boundary blocks reading.

`needs_verification` is an internal backend state, not a request for another
user click. An independent reader/visual pass sets it to `verified` only after
checking complete border, crop edges, annotations, blocks, dimensions, view
roles, and source-to-render coverage.

## Semantic inventory

Build semantics from CAD text entities before topology work:

1. join title fragments only when baseline, layer, spacing, and title grammar
   support the join;
2. retain raw title, canonical title, scale, source text IDs, and confidence;
3. derive one unique `qa_view` for every model-driving view;
4. derive cardinal, axis-range, or section-line direction keys from the
   canonical title;
5. index axis labels and terminal labels, level datums, dimensions, annotation
   counts, and evidence Handles;
6. block duplicate QA keys, unresolved roles, missing canonical titles, or an
   elevation/section without a high-confidence direction key.

Do not OCR-replace structured CAD text. Use visual reading only to check that
the structured result is visible and meaningful in the rendered page.

## Independent reader audit

Keep generation and acceptance separate. Generate a review record conforming
to `contracts/cad-reader-review.schema.json`. Bind it to the candidate package
SHA-256 and require a reviewer derivation different from the render engine.

The independent review must check every complete frame and nested view for
border completeness, title visibility, annotation readability, axis bubbles
when present, clipping, view separation, and inventory completeness. Every
remaining crop-edge Handle needs a specific explanation. Store the review hash
in the promoted package. Only the promoted package may create `source-index`.

## Reading blockers

Block `source-index` when:

- a frame or model-driving view is incomplete or clipped;
- annotations are unreadable at the supplied review resolution;
- expected dimensions, text, attributes, blocks, or opening subdivisions are
  absent from the render;
- a mixed frame has collapsed into one generic view;
- a mixed-view separator crosses protected geometry or uses the title-only
  fallback;
- a view has no assigned geometry cluster or low segmentation confidence;
- canonical titles, unique QA keys, role/direction keys, or semantic evidence
  are missing;
- the independent review is absent, stale, self-referential, or incomplete;
- a view lacks stable bounds, QA key, or source entity IDs;
- required plan/elevation/section roles are absent;
- any model-driving ambiguity remains unresolved.

If the user says CAD contains an omitted element, treat this as a reader-chain
failure and audit extraction before requesting manual dimensions.
