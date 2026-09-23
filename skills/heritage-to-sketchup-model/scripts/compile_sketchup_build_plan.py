#!/usr/bin/env python3
"""Compile verified production details into an executable SketchUp plan."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any
from opening_panels import validate_panels
from material_assets import validate_material_asset

from contract_validation import load_json, validate_contract, validate_schema_file
from create_sketchup_build_derivation import SCHEMA as DERIVATION_SCHEMA, resolve, sha256_file, topology_ids


PLAN_SCHEMA = "cad_to_sketchup.sketchup_production_plan.2026-08-06"


def _project_path(project: Path, value: str, label: str) -> Path:
    path = resolve(project, value)
    try:
        path.relative_to(project.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the project root") from exc
    return path


def _exact_ids(records: list[dict[str, Any]], key: str, expected: set[str], label: str, blockers: list[str]) -> None:
    actual = [str(item.get(key) or "") for item in records]
    if set(actual) != expected or len(actual) != len(set(actual)):
        blockers.append(f"{label} must cover each expected ID exactly once; expected={sorted(expected)} actual={sorted(actual)}")


def compile_plan(project: Path, derivation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    blockers = [f"{item.code}@{item.location}: {item.message}" for item in validate_schema_file("sketchup-build-derivation.schema.json", derivation)]
    if derivation.get("schema") != DERIVATION_SCHEMA or derivation.get("status") != "ready_for_compile":
        blockers.append("production derivation must have status ready_for_compile")
    if derivation.get("blocking_reasons"):
        blockers.extend(str(item) for item in derivation["blocking_reasons"])
    if derivation.get("unresolved"):
        blockers.append("production derivation has unresolved items")
    if not str((derivation.get("derivation") or {}).get("id") or ""):
        blockers.append("production derivation ID is missing")

    upstream = next((item for item in derivation.get("upstream") or [] if item.get("kind") == "building-topology"), None)
    topology_path = resolve(project, (upstream or {}).get("path"))
    if not topology_path.is_file():
        raise ValueError("Confirmed building-topology artifact is missing")
    if sha256_file(topology_path).lower() != str((upstream or {}).get("sha256") or "").lower():
        raise ValueError("Confirmed building-topology artifact is stale")
    topology = load_json(topology_path)
    blockers.extend(f"{item.code}@{item.location}: {item.message}" for item in validate_contract(project, "building-topology", topology))

    opening_ids = {str(item["id"]) for item in topology.get("openings") or []}
    curtain_ids = {str(item["id"]) for item in topology.get("curtain_walls") or []}
    curtain_by_id = {str(item["id"]): item for item in topology.get("curtain_walls") or []}
    material_ids = {str(item["id"]) for item in topology.get("materials") or []}
    geometry_ids = set(topology_ids(topology, include_materials=False))
    opening_specs = derivation.get("opening_assemblies") or []
    curtain_specs = derivation.get("curtain_wall_systems") or []
    material_specs = derivation.get("material_assignments") or []
    _exact_ids(opening_specs, "topology_id", opening_ids, "opening assemblies", blockers)
    _exact_ids(curtain_specs, "topology_id", curtain_ids, "curtain wall systems", blockers)
    _exact_ids(material_specs, "material_id", material_ids, "material assignments", blockers)

    for label, records in (("opening", opening_specs), ("curtain wall", curtain_specs)):
        for record in records:
            item_id = record.get("topology_id")
            if record.get("status") != "verified" or not record.get("family_id"):
                blockers.append(f"{label} {item_id} specification is not verified")
            for field in ("frame_width_mm", "frame_depth_mm", "inset_mm"):
                value = record.get(field)
                if not isinstance(value, (int, float)) or (field != "inset_mm" and value <= 0) or (field == "inset_mm" and value < 0):
                    blockers.append(f"{label} {item_id} has invalid {field}")
            if record.get("panel_type") == "unspecified" or not record.get("evidence"):
                blockers.append(f"{label} {item_id} lacks source-proven panel/detail evidence")
            if label == "curtain wall":
                boundary = (curtain_by_id.get(str(item_id)) or {}).get("boundary") or []
                if len(boundary) != 4:
                    blockers.append(f"curtain wall {item_id} production grid currently requires a four-corner planar boundary")
                elif any(abs(float(boundary[2][axis]) - (float(boundary[1][axis]) + float(boundary[3][axis]) - float(boundary[0][axis]))) > 1.0 for axis in range(3)):
                    blockers.append(f"curtain wall {item_id} boundary is not an ordered planar parallelogram; segment it into source-proven panels before production")

    openings_by_id = {str(item["id"]): item for item in topology.get("openings") or []}
    family_signatures: dict[str, tuple[Any, ...]] = {}
    for record in opening_specs:
        opening = openings_by_id.get(str(record.get("topology_id"))) or {}
        blockers.extend(f"opening {record.get('topology_id')}: {error}" for error in validate_panels(opening, record))
        signature = (
            opening.get("type"), opening.get("width_mm"), opening.get("height_mm"),
            record.get("frame_width_mm"), record.get("frame_depth_mm"), record.get("inset_mm"),
            record.get("panel_type"), tuple(record.get("mullion_ratios") or []), tuple(record.get("transom_ratios") or []),
            json.dumps(record.get("panel_rectangles_mm"), sort_keys=True),
        )
        family_id = str(record.get("family_id") or "")
        if family_id in family_signatures and family_signatures[family_id] != signature:
            blockers.append(f"opening family {family_id} is assigned to inconsistent dimensions or subdivisions")
        family_signatures[family_id] = signature

    for record in material_specs:
        material_id = record.get("material_id")
        blockers.extend(f"material {material_id}: {error}" for error in validate_material_asset(project, record))
        if record.get("status") != "verified" or not record.get("display_name") or record.get("rgba") is None:
            blockers.append(f"material {material_id} specification is not verified")
        targets = set(record.get("target_topology_ids") or [])
        if not targets or not targets.issubset(geometry_ids):
            blockers.append(f"material {material_id} targets are empty or reference unknown topology IDs")
        if not record.get("evidence"):
            blockers.append(f"material {material_id} lacks CAD annotation evidence")

    target = derivation.get("target") or {}
    source_model = _project_path(project, str(target.get("source_model_path") or ""), "target.source_model_path")
    if not source_model.is_file() or sha256_file(source_model).lower() != str(target.get("source_model_sha256") or "").lower():
        blockers.append("source white-model SKP is missing or stale")
    output_model = _project_path(project, str(target.get("output_model_path") or ""), "target.output_model_path")
    result_path = _project_path(project, str(target.get("result_path") or ""), "target.result_path")
    report_path = _project_path(project, str(target.get("report_path") or ""), "target.report_path")
    if output_model.suffix.lower() != ".skp" or output_model == source_model:
        blockers.append("output model must be a new .skp path")
    if result_path.suffix.lower() != ".json" or report_path.suffix.lower() != ".json":
        blockers.append("result and report outputs must be JSON files")
    if len({source_model, output_model, result_path, report_path}) != 4:
        blockers.append("source, model, result, and report paths must be distinct")
    if blockers:
        raise ValueError("SketchUp production plan remains blocked:\n- " + "\n- ".join(blockers))

    plan = {
        "schema": PLAN_SCHEMA,
        "contract_id": derivation["contract_id"],
        "project_id": derivation["project_id"],
        "revision": derivation["revision"],
        "status": "ready_for_sketchup",
        "execution_allowed": True,
        "build_plan_id": f"{derivation['contract_id']}-plan",
        "project_root": str(project.resolve()),
        "upstream": copy.deepcopy(derivation["upstream"]),
        "derivation": copy.deepcopy(derivation["derivation"]),
        "target": copy.deepcopy(target),
        "required_topology_ids": topology_ids(topology),
        "opening_assemblies": copy.deepcopy(opening_specs),
        "curtain_wall_systems": copy.deepcopy(curtain_specs),
        "material_assignments": copy.deepcopy(material_specs),
        "generator": {"module": "HEBIProductionBuild", "root_group": "HEBI_PRODUCTION_BUILD_2026_08_06", "version": "2026-08-06"},
        "unresolved": [],
    }
    plan_issues = validate_schema_file("sketchup-production-plan.schema.json", plan)
    if plan_issues:
        raise ValueError("Compiled SketchUp production plan is invalid:\n- " + "\n- ".join(f"{item.code}@{item.location}: {item.message}" for item in plan_issues))
    return plan, topology


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile a verified SketchUp production plan.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--derivation", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    derivation_path = resolve(project, args.derivation)
    output = resolve(project, args.out)
    try:
        plan, topology = compile_plan(project, load_json(derivation_path))
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc))
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": plan["status"], "required_topology_ids": len(plan["required_topology_ids"]), "levels": len(topology.get("levels") or [])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
