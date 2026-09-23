> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# Topology Confirmation Boundary

Use this gate immediately before the first user confirmation. The user reviews
visible architectural meaning; the backend remains responsible for completeness
and evidence. Never ask the user to remember this checklist or infer what a
white model is supposed to contain.

## Required presentation

State in plain language:

1. `This confirmation accepts only architectural topology, not final rendering.`
2. `Check these items:` then list every mandatory review item below with its
   source status and white-model status.
3. `You may ignore these items for now:` then list the allowed omissions below.
4. `Detected in CAD but missing from the white model:` followed by `none` or
   the blocking items. Never offer confirmation when this list is non-empty.
5. Offer only: confirm and continue; identify a mismatch; stop.

Show useful plan, four elevations, required sections, and an axonometric view.
Do not hide zero counts. A statement such as `canopies: 0` must be accompanied
by independently reviewed `not_applicable` source evidence; a boolean
`canopies_complete: true` is insufficient.

## Must review

- `envelope_and_levels`: footprint, projections, recesses, setbacks, heights;
- `wall_roof_closure`: every exterior wall reaches the roof/parapet underside;
- `slabs_and_roofs`: limits, slopes, skylights, and no slab projection;
- `openings`: ordinary door/window positions, sizes, counts, and true voids;
- `curtain_walls`: facade location, boundary, and host relationship;
- `sweeps_and_cornices`: path, datum, profile, corner continuity, termination;
- `canopies`: footprint, projection, thickness, edge form, and attachment;
- `entrance_steps_and_ramps`: location, width, rise/run, landing, and attachment;
- `material_zone_boundaries`: which facade regions receive each material;
- `interior_wall_terminations`: stop at exterior-wall inner faces without
  facade seams.

## May ignore until production

- exact RGB color, gloss, reflectance, and texture;
- glass transparency and reflection;
- fine frame/mullion subdivisions and hardware;
- rendering, entourage, and presentation styling.

Material color may be ignored, but its coverage boundary may not. Decorative
appearance may be ignored, but any measured projection, recess, profile,
thickness, path, or termination remains topology.

## Machine gate

Require `topology-review.json.confirmation_boundary` to contain exactly the ten
mandatory keys above. For each key, record `source_status`,
`white_model_status`, evidence view IDs, and a concise note. If source status is
`present`, white-model status must be `present`. If source status is
`not_applicable`, retain evidence explaining that conclusion. Require the four
allowed-omission keys exactly and require `cad_detected_but_missing` to be
empty before promotion.
