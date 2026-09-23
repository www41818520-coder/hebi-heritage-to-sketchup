#!/usr/bin/env python3
"""Create independent review templates and promote verified machine QA to the final contract."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from contract_validation import load_json, validate_contract, validate_schema_file
from evaluate_independent_qa import RESULT_SCHEMA
from independent_qa_common import assert_hashed_artifact, ensure_inside, relative, resolve, sha256_file


REVIEW_SCHEMA = "cad_to_sketchup.independent_qa_review.2026-08-06"
FINAL_SCHEMA = "cad_to_sketchup.independent_qa.2026-08-06"
VIEW_CHECKS = ("full_view_unclipped", "same_orientation", "same_scale", "silhouette", "opening_outlines", "true_openings", "levels", "materials", "no_unsupported_geometry")
CROSS_CHECKS = ("every_floor_plan", "four_facades", "required_sections", "plan_elevation_registration", "plan_section_registration", "opposite_facade_orientation", "corner_continuity", "vertical_datum_consistency", "component_reuse", "canopy_assembly", "parapet_roof_silhouette", "material_junction_continuity", "untracked_geometry_clear")


def create_review_template(project: Path, machine_path: Path, machine: dict[str, Any]) -> dict[str, Any]:
    issues = validate_schema_file("independent-qa-machine-result.schema.json", machine)
    if issues or machine.get("schema") != RESULT_SCHEMA or machine.get("status") != "PASS" or machine.get("unresolved"):
        raise ValueError("Machine QA must pass with no unresolved discrepancies before visual review")
    return {
        "schema": REVIEW_SCHEMA, "status": "review_required",
        "machine_result": {"path": relative(project, machine_path), "sha256": sha256_file(machine_path)},
        "reviewer": {"id": "", "derivation_id": ""},
        "view_results": [{"source_view_id": item["source_view_id"], "status": "REVIEW", "checks": {key: False for key in VIEW_CHECKS}, "notes": ""} for item in machine["view_results"]],
        "cross_view_checks": {key: False for key in CROSS_CHECKS}, "unresolved": []
    }


def promote(project: Path, plan_path: Path, plan: dict[str, Any], machine_path: Path, machine: dict[str, Any], review_path: Path, review: dict[str, Any]) -> dict[str, Any]:
    errors = [f"{x.code}@{x.location}: {x.message}" for x in validate_schema_file("independent-qa-plan.schema.json", plan)]
    errors += [f"{x.code}@{x.location}: {x.message}" for x in validate_schema_file("independent-qa-machine-result.schema.json", machine)]
    errors += [f"{x.code}@{x.location}: {x.message}" for x in validate_schema_file("independent-qa-review.schema.json", review)]
    if machine.get("status") != "PASS" or machine.get("unresolved"): errors.append("Machine QA is not PASS")
    recorded_machine = review.get("machine_result") or {}
    try:
        if assert_hashed_artifact(project, recorded_machine, "review machine result") != machine_path.resolve(): errors.append("Review points to a different machine result")
    except ValueError as exc: errors.append(str(exc))
    reviewer = review.get("reviewer") or {}; reviewer_id = str(reviewer.get("derivation_id") or "")
    forbidden = {str(value) for value in (machine.get("derivations") or {}).values()}
    if not reviewer_id or reviewer_id in forbidden: errors.append("Independent reviewer derivation must differ from CAD and model derivations")
    if review.get("status") != "verified" or review.get("unresolved"): errors.append("Independent review is not verified or has unresolved items")
    machine_views = {str(item["source_view_id"]): item for item in machine.get("view_results") or []}
    review_views = {str(item.get("source_view_id") or ""): item for item in review.get("view_results") or []}
    if set(machine_views) != set(review_views) or len(review_views) != len(review.get("view_results") or []): errors.append("Independent review must cover every machine QA view exactly once")
    for view_id, item in review_views.items():
        if item.get("status") != "PASS" or any((item.get("checks") or {}).get(key) is not True for key in VIEW_CHECKS): errors.append(f"{view_id}: independent view review is incomplete")
    if any((review.get("cross_view_checks") or {}).get(key) is not True for key in CROSS_CHECKS): errors.append("Independent cross-view review is incomplete")
    if errors: raise ValueError("Independent QA promotion is blocked:\n- " + "\n- ".join(errors))

    model_path = assert_hashed_artifact(project, machine["model_evidence"], "model evidence")
    model = load_json(model_path)
    cad_derivation_path = next(resolve(project, item["path"]) for item in plan["upstream"] if item.get("kind") == "independent-qa-derivation")
    final_views = []
    for view_id, row in machine_views.items():
        review_row = review_views[view_id]
        final_views.append({
            "source_view_id": view_id, "qa_view": row["qa_view"], "role": row["role"], "status": "PASS",
            "cad_view": row["cad_view"], "model_view": row["model_view"], "overlay": row["overlay"],
            "metrics": {key: row["metrics"][key] for key in ("false_negative_count", "false_positive_count", "max_alignment_delta_mm")},
            "checks": {key: bool(review_row["checks"][key]) for key in VIEW_CHECKS}
        })
    contract = {
        "schema": FINAL_SCHEMA, "contract_id": plan["contract_id"], "project_id": plan["project_id"], "revision": plan["revision"], "status": "PASS", "tolerance_mm": plan["tolerance_mm"],
        "upstream": copy.deepcopy(plan["upstream"]),
        "cad_provenance": {"path": relative(project, cad_derivation_path), "sha256": sha256_file(cad_derivation_path), "derivation_id": plan["derivation"]["id"]},
        "model_provenance": {"path": relative(project, model_path), "sha256": sha256_file(model_path), "derivation_id": model["derivation"]["id"]},
        "machine_result": {"path": relative(project, machine_path), "sha256": sha256_file(machine_path)},
        "independent_review": {"path": relative(project, review_path), "sha256": sha256_file(review_path), "reviewer_derivation_id": reviewer_id},
        "required_views": [item["source_view_id"] for item in plan["views"]], "view_results": final_views,
        "cross_view_checks": {key: bool(review["cross_view_checks"][key]) for key in CROSS_CHECKS},
        "user_confirmation": {"confirmed": False}, "unresolved": []
    }
    issues = validate_contract(project, "independent-qa", contract)
    if issues: raise ValueError("Promoted independent QA contract is invalid:\n- " + "\n- ".join(f"{x.code}@{x.location}: {x.message}" for x in issues))
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or finalize independent QA review.")
    parser.add_argument("--project", required=True, type=Path); parser.add_argument("--machine-result", required=True, type=Path); parser.add_argument("--review-template", type=Path); parser.add_argument("--plan", type=Path); parser.add_argument("--review", type=Path); parser.add_argument("--out", type=Path)
    args = parser.parse_args(); project = args.project.resolve(); machine_path = resolve(project, args.machine_result)
    try:
        machine = load_json(machine_path)
        if args.review_template:
            output = ensure_inside(project, args.review_template, "review template"); value = create_review_template(project, machine_path, machine)
        else:
            if not args.plan or not args.review or not args.out: raise ValueError("Finalization requires --plan, --review, and --out")
            plan_path, review_path = resolve(project, args.plan), resolve(project, args.review); output = ensure_inside(project, args.out, "independent QA contract")
            from review_session import require_receipt
            require_receipt(project, review_path, "qa")
            value = promote(project, plan_path, load_json(plan_path), machine_path, machine, review_path, load_json(review_path))
    except (OSError, ValueError, TypeError, StopIteration) as exc: print(str(exc)); return 1
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": value["status"], "views": len(value["view_results"])}, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
