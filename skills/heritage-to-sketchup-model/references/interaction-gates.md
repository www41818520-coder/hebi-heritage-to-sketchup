> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# Interaction Gates

Choose the review route at intake; see [review.md](review.md). Keep two routine
acceptance decisions: topology and final model. Human review additionally needs
an actual source-reading response before modeling. Combine human topology/final
review and acceptance in one presentation, validating review before acceptance
internally. No second agent is required when a human is the selected reviewer.

## Intake parameters

Collect once:

- approved CAD folder or files;
- units when the CAD header is absent or unreliable;
- modeling scope and expected delivery level;
- optional control mass or closed control polylines.

Do not repeatedly ask the user to approve files, frame numbers, intermediate
JSON, build operations, or QA internals. Show those artifacts for traceability.
Ask a targeted question only when a specific ambiguity can materially change
the model.

## Confirmation 1: topology white model

Ask only after the topology candidate and independent topology review pass.
The user is not expected to remember the topology rules. Read and apply
`topology-confirmation-boundary.md`, then explain the review boundary before
offering confirmation. Never use a bare yes/no confirmation.

Present:

- one readable JPG per complete CAD frame;
- view inventory and any reader warnings;
- topology white model from useful plan and axonometric views;
- floor footprints, continuous exterior wall rings, slab limits, roof, true
  opening locations, curtain walls, sweep paths/profiles, and canopies;
- concise list of evidence-backed defaults or unresolved conflicts.
- a plain-language checklist of what must be checked and what may be ignored;
- a per-system source/white-model inventory, including explicit zero counts;
- every CAD-detected-but-missing system, which blocks confirmation.

Offer:

1. confirm this topology and start the production build;
2. mark a specific mismatch for topology revision;
3. stop.

Bind confirmation to both the topology-candidate hash and white-model SKP hash.
Record the completed `confirmation_boundary` in `topology-review.json`.
Then promote `building-topology.json` with
`scripts/audit_building_topology.py`. Any source-index, topology candidate,
white-model, or review-evidence change revokes the review and confirmation.

## Confirmation 2: final acceptance

Use only after the `independent-qa` contract itself validates as `PASS`.
Present:

- active SKP identity and hash;
- required same-view overlays;
- omission and unsupported-addition counts;
- cross-view consistency results;
- unresolved list, which must be empty.

Offer:

1. accept the verified model;
2. mark a specific mismatch and return to correction;
3. stop without delivery.

User acceptance cannot waive a failed machine gate. Conversely, a machine
PASS cannot override a user-reported visible mismatch.

After acceptance, run `accept_independent_qa.py` with the user's exact
instruction and a stable confirmation ID. The script binds acceptance to the
current active SKP, machine result, independent review, and pre-acceptance QA
fingerprint. Then create the delivery package, run the delivery gate, and issue
the `DELIVERED` receipt with `finalize_delivery.py`. Only that receipt may
authorize the delivered state.

## Exception questions

Interrupt outside the two confirmations only when all safe evidence paths are
exhausted and the answer materially changes topology or visible production
geometry. State:

1. the exact frame/view/element;
2. the conflicting evidence;
3. the proposed interpretation;
4. the consequence of each option.

If CAD visibly contains missing geometry, classify it as a reader or extraction
failure and repair that chain before asking the user for dimensions.
