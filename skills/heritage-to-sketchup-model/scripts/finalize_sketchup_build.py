#!/usr/bin/env python3
"""Promote a real SketchUp production result into the sketchup-build contract."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from compile_sketchup_build_plan import PLAN_SCHEMA
from contract_validation import SCHEMAS, load_json, validate_contract, validate_schema_file
from create_sketchup_build_derivation import relative, resolve, sha256_file


RESULT_SCHEMA = "cad_to_sketchup.sketchup_production_result.2026-08-06"


def _artifact(project: Path, value: Any, expected_hash: Any, label: str, errors: list[str]) -> dict[str, str] | None:
    path = resolve(project, value)
    if not path.is_file():
        errors.append(f"{label} is missing: {path}")
        return None
    actual = sha256_file(path)
    if actual.lower() != str(expected_hash or "").lower():
        errors.append(f"{label} hash is stale")
    return {"path": relative(project, path), "sha256": actual}


def finalize(project: Path, plan_path: Path, plan: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    errors.extend(f"{item.code}@{item.location}: {item.message}" for item in validate_schema_file("sketchup-production-plan.schema.json", plan))
    errors.extend(f"{item.code}@{item.location}: {item.message}" for item in validate_schema_file("sketchup-production-result.schema.json", result))
    if plan.get("schema") != PLAN_SCHEMA or plan.get("status") != "ready_for_sketchup" or plan.get("execution_allowed") is not True:
        errors.append("SketchUp production plan is not execution-authorized")
    if result.get("schema") != RESULT_SCHEMA or result.get("status") != "generated_and_self_checked":
        errors.append("SketchUp production result schema or status is invalid")
    recorded_plan = resolve(project, result.get("build_plan_path"))
    if recorded_plan != plan_path.resolve() or not recorded_plan.is_file():
        errors.append("Production result points to a different build plan")
    plan_hash = sha256_file(plan_path)
    if plan_hash.lower() != str(result.get("build_plan_sha256") or "").lower():
        errors.append("Production result is stale relative to build plan")

    topology_ref = next((item for item in plan.get("upstream") or [] if item.get("kind") == "building-topology"), None)
    topology_path = resolve(project, (topology_ref or {}).get("path"))
    if not topology_path.is_file() or sha256_file(topology_path).lower() != str((topology_ref or {}).get("sha256") or "").lower():
        errors.append("Confirmed building topology is missing or stale")
        topology = {}
    else:
        topology = load_json(topology_path)
        errors.extend(f"{item.code}@{item.location}: {item.message}" for item in validate_contract(project, "building-topology", topology))

    source = _artifact(project, result.get("source_model_path"), result.get("source_model_sha256"), "source white model", errors)
    output = _artifact(project, result.get("output_model_path"), result.get("output_model_sha256"), "production SKP", errors)
    report = _artifact(project, result.get("report_path"), result.get("report_sha256"), "production self-check report", errors)
    target = plan.get("target") or {}
    if source and source["sha256"].lower() != str(target.get("source_model_sha256") or "").lower():
        errors.append("Production result did not start from the plan's confirmed white model")
    if output and resolve(project, target.get("output_model_path")) != resolve(project, output["path"]):
        errors.append("Production result was saved to a different output model")

    required_ids = list(plan.get("required_topology_ids") or [])
    result_items = result.get("element_results") or []
    result_ids = [str(item.get("topology_id") or "") for item in result_items]
    if set(result_ids) != set(required_ids) or len(result_ids) != len(set(result_ids)):
        errors.append("Production result must contain exactly one result for every topology ID")
    if any(item.get("status") != "PASS" or item.get("failed_solids") != 0 or not item.get("sketchup_entity_ids") for item in result_items):
        errors.append("One or more production elements failed or lack persistent entity IDs")
    checks = result.get("geometry_checks") or {}
    if not checks or any(value is not True for value in checks.values()):
        errors.append("Production geometry self-check did not fully pass")
    if result.get("unresolved"):
        errors.append("Production result contains unresolved defects")
    if errors:
        raise ValueError("Cannot finalize SketchUp build:\n- " + "\n- ".join(errors))

    contract = {
        "schema": SCHEMAS["sketchup-build"],
        "contract_id": plan["contract_id"],
        "project_id": plan["project_id"],
        "revision": plan["revision"],
        "status": "verified",
        "upstream": copy.deepcopy(plan["upstream"]),
        "generator": {
            "module": plan["generator"]["module"], "version": plan["generator"]["version"],
            "root_group": result["root_group"], "root_persistent_id": result["root_persistent_id"],
        },
        "build_plan": {"path": relative(project, plan_path), "sha256": plan_hash},
        "execution_report": report,
        "active_model": output,
        "required_topology_ids": required_ids,
        "element_results": copy.deepcopy(result_items),
        "statistics": copy.deepcopy(result["statistics"]),
        "geometry_checks": copy.deepcopy(checks),
        "unresolved": [],
    }
    final_issues = validate_contract(project, "sketchup-build", contract)
    if final_issues:
        raise ValueError("Cannot finalize SketchUp build:\n- " + "\n- ".join(f"{item.code}@{item.location}: {item.message}" for item in final_issues))
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize a SketchUp production build contract.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    plan_path = resolve(project, args.plan)
    result_path = resolve(project, args.result)
    output = resolve(project, args.out)
    try:
        contract = finalize(project, plan_path, load_json(plan_path), load_json(result_path))
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc))
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": contract["status"], "model": contract["active_model"]["path"], "elements": len(contract["element_results"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
