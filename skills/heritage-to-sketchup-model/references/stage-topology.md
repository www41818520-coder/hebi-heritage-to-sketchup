> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# 2. Build and confirm the topology white model

Apply [review.md](review.md). For human review present the full boundary and
collect review/approval together; validate review before promotion internally.
Independence means a different actual reviewer, not a renamed producer.

Derive one shared building system from all confirmed views before adding
facade detail. Plans control XY; elevations control facade organization and
vertical datums; sections control depth and vertical construction
relationships. A reference control mass may fix basic envelope topology but
does not supply openings, moldings, canopies, or facade detail.

Read [references/building-topology-contract.md](building-topology-contract.md)
for the complete candidate, independent-review, user-confirmation, and repair
workflow. Read
[references/facade-direction-and-envelope-review.md](facade-direction-and-envelope-review.md)
when registering directions and reconciling envelope evidence. Apply these
hard rules:

- represent every floor with its true footprint, including first-floor
  projections and setbacks;
- create each floor's exterior wall perimeter as one closed continuous group;
- terminate every exterior wall against the CAD-confirmed roof/parapet profile;
  when the contact is sloped or stepped, require a measured full-length wall
  `top_profile` and forbid interior exposure below the roof;
- keep slabs inside or exactly on the owning floor envelope;
- remove overlapping plan wall faces where the exterior envelope owns the
  same surface;
- stop interior walls at the exterior wall inner face and record the host-wall
  termination; never allow their end faces to become facade lines;
- erase coplanar wall-block construction seams after forming true openings,
  while preserving real jamb, head, sill, silhouette, and corner edges;
- register each elevation using formal title, outward direction, terminal
  axes, and at least two asymmetric anchors;
- model ordinary doors/windows as true through-cuts in their host walls;
- use actual wall thickness for cut depth; only when absent, use the documented
  200 mm ordinary-opening default; treat curtain walls separately;
- represent facade moldings/cornices as a continuous path plus complete
  section profile, datum, corner rule, and termination evidence;
- derive canopy thickness and edge form from at least two orthogonal
  elevations; never assign a default canopy thickness;
- represent CAD-proven skylight strips and roof openings as `roof_lights`
  hosted by a measured roof, with a 3D boundary on the roof plane, system
  type, true-opening state, and roof-plan plus section/elevation evidence;
- treat color and material text as surface evidence unless measurable geometry
  proves a projection, recess, profile, or thickness.

Write decisions into a hash-bound topology candidate, never only into chat.
Use `create_topology_derivation.py` to inventory every model-driving view,
fill and verify its evidence-bound registrations and topology, then use
`compile_topology_plan.py`. Run `workflow_gate.py --stage topology` before
invoking `build_topology_white_model.rb` through the SketchUp bridge. Convert
the real SKP/result/review JPGs into the candidate with
`finalize_topology_candidate.py`.

Generate the topology white model in the same SKP that will continue to final
modeling, and export one review view for every registered source view. A
different derivation must complete `topology-review.json`; the topology agent
cannot self-promote. Before asking for the first confirmation, read and apply
[references/topology-confirmation-boundary.md](topology-confirmation-boundary.md).
Never ask only "confirm the topology white model?" Present the required-review
boundary, allowed-to-ignore boundary, current topology inventory, and every
CAD-detected-but-missing item. Require the independent reviewer to complete
`confirmation_boundary`; block confirmation when any source-present item is
absent from the white model. Ask for confirmation only after that inventory
and the independent topology review pass. Bind confirmation to both the
white-model hash and candidate hash, then promote `building-topology.json`
with `audit_building_topology.py`.

Any changed candidate, white-model SKP, review image, source-index, or user
confirmation invalidates promotion. Any topology mismatch found later must be
fixed in the topology candidate first, not patched only in final SketchUp
geometry.

After topology confirmation and promotion, before production detailing run:

```powershell
python scripts/workflow_gate.py --project <project> --stage build
```
