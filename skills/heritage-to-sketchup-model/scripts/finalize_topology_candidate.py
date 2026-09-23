#!/usr/bin/env python3
"""Bind a compiled topology plan to an actual white-model result."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from audit_building_topology import CANDIDATE_SCHEMA, candidate_errors, core_validation_errors, sha256_file
from compile_topology_plan import CORE_KEYS, PLAN_SCHEMA
from contract_validation import load_json, validate_schema_file


RESULT_SCHEMA = "cad_to_sketchup.topology_white_model_result.2026-08-05"


def resolve(project: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return path if path.is_absolute() else project / path


def relative(project: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def finalize(project: Path, plan_path: Path, plan: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    errors.extend(f"{item.code}@{item.location}: {item.message}" for item in validate_schema_file("topology-white-model-plan.schema.json", plan))
    errors.extend(f"{item.code}@{item.location}: {item.message}" for item in validate_schema_file("topology-white-model-result.schema.json", result))
    if plan.get("schema") != PLAN_SCHEMA or plan.get("status") != "ready_for_white_model" or plan.get("execution_allowed") is not True:
        errors.append("topology plan is not execution-authorized")
    errors.extend(core_validation_errors(project, plan))
    if result.get("schema") != RESULT_SCHEMA or result.get("status") != "generated":
        errors.append("white-model result schema or status is invalid")
    plan_hash = sha256_file(plan_path)
    if str(result.get("topology_plan_sha256") or "").lower() != plan_hash.lower():
        errors.append("white-model result is stale relative to topology plan")
    recorded_plan = resolve(project, result.get("topology_plan_path"))
    if recorded_plan is None or not recorded_plan.is_file() or recorded_plan.resolve() != plan_path.resolve():
        errors.append("white-model result points to a different topology plan")
    model_path = resolve(project, result.get("model_path"))
    if model_path is None or not model_path.is_file():
        errors.append("topology white-model SKP is missing")
    elif sha256_file(model_path).lower() != str(result.get("model_sha256") or "").lower():
        errors.append("topology white-model SKP hash is stale")
    expected = {str(item.get("source_view_id")): item for item in plan.get("registrations") or []}
    review_views = result.get("review_views") or []
    result_ids = [str(item.get("source_view_id") or "") for item in review_views]
    if set(result_ids) != set(expected) or len(result_ids) != len(set(result_ids)):
        errors.append("white-model result must contain exactly one image for every registered source view")
    normalized_views = []
    for index, view in enumerate(review_views):
        view_id = str(view.get("source_view_id") or "")
        expected_registration = expected.get(view_id) or {}
        if view.get("role") != expected_registration.get("role"):
            errors.append(f"review view {view_id} role does not match registration")
        path = resolve(project, view.get("path"))
        if path is None or not path.is_file():
            errors.append(f"review view {view_id} is missing")
            continue
        if sha256_file(path).lower() != str(view.get("sha256") or "").lower():
            errors.append(f"review view {view_id} hash is stale")
        normalized_views.append({
            "source_view_id": view_id,
            "role": view.get("role"),
            "path": relative(project, path),
            "sha256": view.get("sha256"),
        })
    if result.get("unresolved"):
        errors.append("white-model generation has unresolved findings")
    if errors:
        raise ValueError("Cannot finalize topology candidate:\n- " + "\n- ".join(errors))

    candidate = {key: copy.deepcopy(plan.get(key)) for key in CORE_KEYS}
    candidate["schema"] = CANDIDATE_SCHEMA
    candidate["status"] = "candidate"
    candidate["white_model"] = {
        "model_path": relative(project, model_path),
        "model_sha256": result.get("model_sha256"),
        "stage": "topology_white_model",
        "review_views": normalized_views,
    }
    candidate.pop("topology_audit", None)
    candidate.pop("user_confirmation", None)
    final_errors = candidate_errors(project, candidate)
    if final_errors:
        raise ValueError("Cannot finalize topology candidate:\n- " + "\n- ".join(final_errors))
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize a topology candidate from the actual white-model result.")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--white-model-result", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    plan_path = args.plan if args.plan.is_absolute() else project / args.plan
    result_path = args.white_model_result if args.white_model_result.is_absolute() else project / args.white_model_result
    output = args.out if args.out.is_absolute() else project / args.out
    try:
        candidate = finalize(project, plan_path.resolve(), load_json(plan_path), load_json(result_path))
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc))
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": candidate["status"], "model": candidate["white_model"]["model_path"], "review_views": len(candidate["white_model"]["review_views"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
