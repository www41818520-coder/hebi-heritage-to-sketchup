> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# 5. Deliver, invalidate, and learn

Read [references/delivery-invalidation-learning.md](delivery-invalidation-learning.md).
First validate the completed independent QA contract:

```powershell
python scripts/validate_contract.py `
  --project <project> `
  --kind independent-qa `
  --input work/contracts/independent-qa.json
```

Only after that contract returns `PASS`, show the final model and independent
evidence, then request the second user confirmation. Bind the user's exact
instruction to the current model and evidence hashes with
`accept_independent_qa.py`; never toggle the confirmation field manually.
Create a non-overwriting timestamped SKP, exact evidence manifest, and report
with `create_delivery_package.py`, then run:

```powershell
python scripts/workflow_gate.py --project <project> --stage delivery
```

After the delivery gate passes, run `finalize_delivery.py`. Only its immutable
`DELIVERED` receipt authorizes the delivered state. Before the receipt, call
the package only `packaged for delivery gate`. Use
[references/delivery-report-template.md](delivery-report-template.md)
for the report fields.

Until final confirmation, call the result only `candidate`, `topology white
model`, `build under QA`, or `ready for acceptance`. Never call it final or
delivered.

If any user-visible mismatch is reported:

1. revoke the delivery claim immediately;
2. invalidate the earliest affected contract and every downstream contract;
3. correct the reader, topology, or SketchUp operation at its source;
4. rerun the complete affected QA view set;
5. use `invalidate_workflow.py` to preserve a pre-invalidation state snapshot
   and remove the earliest failed active reference plus every downstream one;
6. record the correction in this project's report. Add regression tests when a
   reusable algorithm changes, not for every ordinary modeling adjustment.

Skill maintenance is separate and optional: only when the user requests a skill
update, use `record_skill_mark.py` to bind a transferable fix to actual test
evidence. Do not rewrite a user's installed skill during routine modeling.

Never delete historical contracts, SKPs, manifests, or delivery receipts when
invalidating. They are audit history, not current authorization.
