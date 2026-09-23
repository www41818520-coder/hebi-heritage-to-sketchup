> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# Facade Direction And Envelope Review

Read this reference when paired elevations may be mirrored or when comparing a candidate with a handmade/reference shell.

## Direction Contract

For every elevation, record:

| Field | Meaning |
|---|---|
| drawing title | Formal elevation title |
| drawing left/right axes | Terminal axes as printed |
| model side | North, south, east, or west |
| outward view vector | Direction from building toward viewer |
| screen-right model vector | Model-axis direction appearing right in the exterior orthographic view |
| coordinate conversion | The single named drawing-horizontal to model-coordinate conversion |
| asymmetric anchors | At least two unequal or unique features used to prove handedness |

Project the generated anchor sequence back onto the complete elevation. Compare ordered features, not only counts.

Common failure: extraction reverses a `J-A` or `13-1` drawing into model coordinates, then placement or review reverses it again. The dimensions remain correct while the facade becomes mirrored.

## Reference-Shell Review

1. Keep candidate and reference separate and read-only.
2. Inspect top-level and nested transform determinants.
3. Align units, datum, axes, orientation, and roof ridge before comparing.
4. Compare complete plans and all four orthographic elevations.
5. Review construction logic:
   - corner returns and material-band continuity;
   - wall-top to roof/eave contact;
   - eave projection, fascia thickness, and end caps;
   - canopy thickness, supports, wall connection, and landing/ramp relationship;
   - window/door reveal depth and frame hierarchy;
   - cladding seams and component reuse.
6. Classify each difference:
   - source-supported correction;
   - reference-inspired hypothesis requiring confirmation;
   - incidental detail to ignore.

Never infer material from CAD display color alone. Never copy reference dimensions that cannot be traced to drawings or explicit user approval.
