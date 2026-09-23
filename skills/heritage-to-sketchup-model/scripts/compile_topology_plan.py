#!/usr/bin/env python3
"""Compile a filled topology derivation into a white-model execution plan."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from audit_building_topology import core_validation_errors
from contract_validation import load_json, validate_schema_file
from create_topology_derivation import SCHEMA as DERIVATION_SCHEMA


PLAN_SCHEMA = "cad_to_sketchup.topology_white_model_plan.2026-08-05"
CORE_KEYS = (
    "contract_id", "project_id", "revision", "upstream", "derivation",
    "coordinate_system", "registrations", "control_basis", "levels", "walls", "internal_walls",
    "slabs", "roofs", "roof_lights", "openings", "curtain_walls", "sweeps", "canopies",
    "materials", "unresolved", "access_elements", "columns",
)
REGISTRATION_KEYS = ("id", "source_view_id", "qa_view", "role", "source_bounds", "section_cut_elevation_mm", "transform", "visible_topology_ids", "anchor_evidence", "status")


def _project_output(project: Path, value: str, label: str) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else project / path).resolve()
    try:
        resolved.relative_to(project.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the project root: {resolved}") from exc
    return resolved


def compile_plan(project: Path, derivation: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    schema_issues = validate_schema_file("topology-derivation.schema.json", derivation)
    blockers.extend(f"{item.code}@{item.location}: {item.message}" for item in schema_issues)
    if derivation.get("schema") != DERIVATION_SCHEMA:
        blockers.append(f"schema must be {DERIVATION_SCHEMA}")
    if derivation.get("status") != "ready_for_compile":
        blockers.append("derivation status must be ready_for_compile")
    if derivation.get("blocking_reasons"):
        blockers.extend(str(item) for item in derivation.get("blocking_reasons") or [])
    if derivation.get("unresolved"):
        blockers.append("model-driving unresolved items remain")
    derivation_id = str((derivation.get("derivation") or {}).get("id") or "")
    if not derivation_id:
        blockers.append("topology derivation ID is missing")
    registrations = derivation.get("registrations") or []
    for registration in registrations:
        if registration.get("status") != "verified":
            blockers.append(f"registration {registration.get('source_view_id')} is not verified")
    if blockers:
        raise ValueError("Topology derivation remains blocked:\n- " + "\n- ".join(blockers))

    plan = {key: copy.deepcopy(derivation.get(key)) for key in CORE_KEYS}
    plan["access_elements"] = copy.deepcopy(derivation.get("access_elements") or [])
    plan["columns"] = copy.deepcopy(derivation.get("columns") or [])
    plan["schema"] = PLAN_SCHEMA
    plan["status"] = "ready_for_white_model"
    plan["execution_allowed"] = True
    plan["topology_plan_id"] = f"{plan.get('contract_id')}-white-model-plan"
    plan["project_root"] = str(project.resolve())
    plan["registrations"] = [
        {
            key: copy.deepcopy(registration.get(key))
            for key in REGISTRATION_KEYS
            if key in registration
        }
        for registration in registrations
    ]
    output = derivation.get("white_model_output") or {}
    required_output = ("model_path", "result_path", "review_dir")
    if not all(isinstance(output.get(key), str) and output.get(key).strip() for key in required_output):
        raise ValueError("white_model_output requires model_path, result_path, and review_dir")
    model_output = _project_output(project, output["model_path"], "white_model_output.model_path")
    result_output = _project_output(project, output["result_path"], "white_model_output.result_path")
    review_output = _project_output(project, output["review_dir"], "white_model_output.review_dir")
    if model_output.suffix.lower() != ".skp":
        raise ValueError("white_model_output.model_path must end with .skp")
    if result_output.suffix.lower() != ".json":
        raise ValueError("white_model_output.result_path must end with .json")
    if len({model_output, result_output, review_output}) != 3:
        raise ValueError("white-model output paths must be distinct")
    plan["white_model_output"] = copy.deepcopy(output)
    semantic_errors = core_validation_errors(project, plan)
    if semantic_errors:
        raise ValueError("Topology plan is not executable:\n- " + "\n- ".join(semantic_errors))
    plan_schema_issues = validate_schema_file("topology-white-model-plan.schema.json", plan)
    if plan_schema_issues:
        raise ValueError("Topology plan schema is invalid:\n- " + "\n- ".join(f"{item.code}@{item.location}: {item.message}" for item in plan_schema_issues))
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile a verified topology derivation into a white-model plan.")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--derivation", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    derivation_path = args.derivation if args.derivation.is_absolute() else project / args.derivation
    output = args.out if args.out.is_absolute() else project / args.out
    try:
        plan = compile_plan(project, load_json(derivation_path))
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc))
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": plan["status"], "registrations": len(plan["registrations"]), "levels": len(plan["levels"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
