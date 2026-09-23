#!/usr/bin/env python3
"""Create a blocked production-specification workspace from confirmed topology."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from contract_validation import load_json, validate_contract, validate_schema_file


SCHEMA = "cad_to_sketchup.sketchup_build_derivation.2026-08-06"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(project: Path, value: Any) -> Path:
    path = Path(str(value))
    return (path if path.is_absolute() else project / path).resolve()


def relative(project: Path, path: Path) -> str:
    return str(path.resolve().relative_to(project.resolve())).replace("\\", "/")


def create_template(project: Path, topology_path: Path, topology: dict[str, Any], output_model: str) -> dict[str, Any]:
    issues = validate_contract(project, "building-topology", topology)
    if issues:
        raise ValueError("Confirmed topology is invalid:\n- " + "\n- ".join(f"{item.code}@{item.location}: {item.message}" for item in issues))
    source_model = resolve(project, topology["white_model"]["model_path"])
    if not source_model.is_file() or sha256_file(source_model).lower() != topology["white_model"]["model_sha256"].lower():
        raise ValueError("Confirmed topology white model is missing or stale")
    output_path = resolve(project, output_model)
    try:
        output_path.relative_to(project.resolve())
    except ValueError as exc:
        raise ValueError("Production output model must stay inside the project root") from exc
    if output_path.suffix.lower() != ".skp" or output_path == source_model:
        raise ValueError("Production output must be a new .skp path")

    blockers: list[str] = []
    opening_specs = []
    for opening in topology.get("openings") or []:
        topology_id = str(opening["id"])
        opening_specs.append({
            "topology_id": topology_id,
            "status": "specification_required",
            "family_id": "",
            "frame_width_mm": None,
            "frame_depth_mm": None,
            "inset_mm": None,
            "panel_type": "unspecified",
            "mullion_ratios": [],
            "transom_ratios": [],
            "evidence": opening.get("evidence") or [],
        })
        blockers.append(f"opening {topology_id}: verify frame, inset, panel type, mullions and transoms from its source view")

    curtain_specs = []
    for curtain in topology.get("curtain_walls") or []:
        topology_id = str(curtain["id"])
        curtain_specs.append({
            "topology_id": topology_id,
            "status": "specification_required",
            "family_id": "",
            "frame_width_mm": None,
            "frame_depth_mm": None,
            "inset_mm": None,
            "panel_type": "unspecified",
            "vertical_ratios": [],
            "horizontal_ratios": [],
            "evidence": curtain.get("evidence") or [],
        })
        blockers.append(f"curtain wall {topology_id}: verify frame dimensions, inset and grid ratios from CAD")

    material_specs = []
    all_geometry_ids = topology_ids(topology, include_materials=False)
    for material in topology.get("materials") or []:
        material_id = str(material["id"])
        material_specs.append({
            "material_id": material_id,
            "status": "specification_required",
            "display_name": "",
            "rgba": None,
            "target_topology_ids": [],
            "evidence": [material["evidence"]],
        })
        blockers.append(f"material {material_id}: resolve annotation value and explicit target topology IDs from {len(all_geometry_ids)} available elements")

    result = {
        "schema": SCHEMA,
        "contract_id": f"{topology['project_id']}-sketchup-build-r1",
        "project_id": topology["project_id"],
        "revision": 1,
        "status": "detail_specification_required" if blockers else "ready_for_compile",
        "upstream": [{"kind": "building-topology", "path": relative(project, topology_path), "sha256": sha256_file(topology_path)}],
        "derivation": {"id": "", "agent_role": "production_specification"},
        "target": {
            "source_model_path": relative(project, source_model),
            "source_model_sha256": sha256_file(source_model),
            "output_model_path": relative(project, output_path),
            "result_path": "work/build/sketchup-production-result.json",
            "report_path": "reports/build/sketchup-production-self-check.json",
        },
        "opening_assemblies": opening_specs,
        "curtain_wall_systems": curtain_specs,
        "material_assignments": material_specs,
        "blocking_reasons": blockers,
        "unresolved": [],
    }
    schema_issues = validate_schema_file("sketchup-build-derivation.schema.json", result)
    if schema_issues:
        raise ValueError("Generated production derivation is invalid:\n- " + "\n- ".join(f"{item.code}@{item.location}: {item.message}" for item in schema_issues))
    return result


def topology_ids(topology: dict[str, Any], include_materials: bool = True) -> list[str]:
    ids: list[str] = []
    for key in ("levels", "walls", "internal_walls", "slabs", "roofs", "roof_lights", "openings", "curtain_walls", "sweeps", "canopies", "access_elements", "columns"):
        ids.extend(str(item["id"]) for item in topology.get(key) or [])
    if include_materials:
        ids.extend(str(item["id"]) for item in topology.get("materials") or [])
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a SketchUp production-specification workspace.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--topology", required=True, type=Path)
    parser.add_argument("--output-model", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    topology_path = resolve(project, args.topology)
    output = resolve(project, args.out)
    try:
        result = create_template(project, topology_path, load_json(topology_path), args.output_model)
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc))
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "openings": len(result["opening_assemblies"]), "curtain_walls": len(result["curtain_wall_systems"]), "materials": len(result["material_assignments"]), "blockers": len(result["blocking_reasons"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
