> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# Five-Step Workflow Contract

Use this reference when creating `work/workflow-state.json` or when a workflow
gate reports a blocker. Detailed field constraints live in `contracts/*.schema.json`;
semantic constraints are enforced by `scripts/contract_validation.py`.

## Contract chain

| Stage | Contract | Must prove | User action |
|---|---|---|---|
| Reading | `source-index` | Complete frames/views, canonical titles, direction/axis/level/dimension inventory, readable evidence, independent reader audit, render coverage, units and source hashes | None unless a concrete exception is found |
| Interpretation | `building-topology-candidate` -> independent `topology-review` -> confirmed `building-topology` | Registered source views, true floor envelopes, ordered continuous wall rings, contained slabs, hosted openings, curtain-wall envelopes, roofs, continuous sweeps, and cross-view canopies | Confirm independently reviewed topology white model |
| Production | `sketchup-build-derivation` -> `sketchup-production-plan` -> `sketchup-build` | Production details are source-proven; every topology ID has persistent entities; statistics and model-internal checks pass | None unless a specific source ambiguity remains |
| Independent verification | QA derivation -> QA plan -> active-SKP evidence -> machine result -> independent review -> `independent-qa` | Exact source-view coverage, same-coordinate overlays, zero omissions/additions, and cross-view agreement | None |
| Delivery | accepted `independent-qa` | QA remains current and the user accepts the verified result | Accept final model |

Each downstream contract contains an `upstream` array:

```json
{
  "kind": "source-index",
  "path": "work/contracts/source-index.json",
  "sha256": "64-character fingerprint"
}
```

If the file hash changes, the downstream validator returns `upstream.stale`.
Never repair this by copying the old hash. Regenerate the affected contract.

## Workflow state

Keep one small state file that points to immutable contract revisions:

```json
{
  "schema": "cad_to_sketchup.workflow.2026-08-04",
  "status": "topology_review",
  "contracts": {
    "source-index": {
      "path": "work/contracts/source-index.json",
      "sha256": ""
    },
    "building-topology": {
      "path": "work/contracts/building-topology.json",
      "sha256": ""
    },
    "sketchup-build": {
      "path": "work/contracts/sketchup-build.json",
      "sha256": ""
    },
    "independent-qa": {
      "path": "work/contracts/independent-qa.json",
      "sha256": ""
    }
  },
  "topology_plan": {
    "path": "work/topology/topology-plan.json",
    "sha256": ""
  },
  "production_plan": {
    "path": "work/build/sketchup-production-plan.json",
    "sha256": ""
  },
  "qa_plan": {
    "path": "work/qa/independent-qa-plan.json",
    "sha256": ""
  },
  "delivery_manifest": {
    "path": "work/contracts/delivery-manifest.json",
    "sha256": ""
  },
  "delivery_receipt": {
    "path": "reports/delivery/receipt-<package>.json",
    "sha256": ""
  },
  "sketchup_target": {
    "confirmed": false,
    "model_path": ""
  }
}
```

Omit contracts that do not exist yet. A missing downstream contract is normal
before its stage and blocking at or after its stage.

## Gate commands

```powershell
python scripts/workflow_gate.py --project <project> --stage reading
python scripts/workflow_gate.py --project <project> --stage topology
python scripts/workflow_gate.py --project <project> --stage build
python scripts/workflow_gate.py --project <project> --stage qa
python scripts/workflow_gate.py --project <project> --stage delivery
```

The `topology` gate is the narrow exception that permits generation of the
pre-confirmation topology white model from a verified, hash-bound plan. It
does not permit production facade detailing. The `build` gate still requires
the independently reviewed, user-confirmed `building-topology` contract plus a
current `production_plan` with exact topology coverage and white-model hash.

The `qa` gate proves the active build is ready for independent checking. After
the `independent-qa` contract validates as `PASS`, bind final acceptance with
`accept_independent_qa.py`, create the manifest and timestamped SKP, then run
the `delivery` gate. Issue the final receipt with `finalize_delivery.py`.

Validate an individual contract while developing it:

```powershell
python scripts/validate_contract.py `
  --project <project> `
  --kind building-topology `
  --input work/contracts/building-topology.json
```

`BLOCKED` is an expected safety result. Fix the earliest failed contract, then
regenerate all dependent contracts.

## Invalidation matrix

| Change | Invalidate |
|---|---|
| CAD source, units, frame, view bounds, role, or render coverage | All four contracts |
| Control mass, floor envelope, facade direction, datum, opening, sweep, canopy, or material geometry | Topology, build, and QA |
| Active SKP or generated entity changes | Build and QA |
| QA tolerance, required view, CAD derivation, or model derivation changes | QA |
| Final acceptance, accepted SKP, evidence file, manifest, or report changes | Delivery package and receipt |
| User rejects topology white model | Topology, build, and QA |
| Topology candidate, white-model SKP, or topology review view changes | Topology review, user topology confirmation, build, and QA |
| User reports a final visible mismatch | Accepted state and earliest affected contract onward |

Invalidation removes only active state references. Preserve the original
contracts, SKPs, manifests, reports, receipts, and a snapshot of the state
before invalidation.

Do not remove a failing required view or topology ID to make a gate pass.
