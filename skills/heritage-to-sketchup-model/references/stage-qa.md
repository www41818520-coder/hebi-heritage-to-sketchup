> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# 4. Run independent QA

Use the selected human or agent route in [review.md](review.md). Neither route
waives machine failures or substitutes perspective inspection for registered QA.

Read [references/independent-qa-contract.md](independent-qa-contract.md)
and execute its four sub-stages. Derive CAD evidence from the verified source
index and model evidence from the active SKP export. Use different files,
generators, and derivation IDs for CAD extraction, model export, and review.

For every model-driving plan, roof plan, elevation, and section:

1. export same-orientation and same-scale model evidence;
2. create a same-view overlay;
3. measure omissions (`false_negative_count`) and unsupported additions
   (`false_positive_count`);
4. check silhouette, opening outlines, true openings, levels, and materials;
5. check plan/elevation registration, plan/section registration, corner
   continuity, and vertical datum consistency.

Full delivery requires zero model-driving omissions and zero unsupported extra
elements in every required view. A shared build constant, pasted CAD, or QA
derived from the build plan itself is self-referential and must fail.

Hard coverage requires every model-driving source view exactly once, one plan
QA view per modeled floor, a roof-plan QA view whenever a roof is modeled, at
least four distinct elevation views, and all required sections. Generate exact
per-view CAD JPGs with `create_independent_qa_derivation.py`, compile with
`compile_independent_qa_plan.py`, export the active SKP read-only with
`export_sketchup_qa_evidence.rb`, evaluate same-coordinate overlays with
`evaluate_independent_qa.py`, and promote only through
`finalize_independent_qa.py` after a different derivation completes the review.
