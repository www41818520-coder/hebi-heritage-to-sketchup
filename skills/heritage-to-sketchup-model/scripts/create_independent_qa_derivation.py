#!/usr/bin/env python3
"""Create exact per-source-view CAD images and a blocked QA expectation workspace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image

from contract_validation import load_json, validate_contract, validate_schema_file
from independent_qa_common import MODEL_ROLES, artifact, ensure_inside, relative, resolve, sha256_file, valid_bounds


SCHEMA = "cad_to_sketchup.independent_qa_derivation.2026-08-06"


def crop_view(frame_image: Path, frame_bounds: dict[str, Any], view_bounds: dict[str, Any], output: Path) -> None:
    if not valid_bounds(frame_bounds) or not valid_bounds(view_bounds):
        raise ValueError("Frame and view bounds must be non-empty")
    with Image.open(frame_image) as source:
        width, height = source.size
        fw = float(frame_bounds["xmax"]) - float(frame_bounds["xmin"])
        fh = float(frame_bounds["ymax"]) - float(frame_bounds["ymin"])
        left = round((float(view_bounds["xmin"]) - float(frame_bounds["xmin"])) / fw * width)
        right = round((float(view_bounds["xmax"]) - float(frame_bounds["xmin"])) / fw * width)
        top = round((float(frame_bounds["ymax"]) - float(view_bounds["ymax"])) / fh * height)
        bottom = round((float(frame_bounds["ymax"]) - float(view_bounds["ymin"])) / fh * height)
        if left < 0 or top < 0 or right > width or bottom > height or right <= left or bottom <= top:
            raise ValueError("A source view lies outside its confirmed complete frame")
        output.parent.mkdir(parents=True, exist_ok=True)
        source.convert("RGB").crop((left, top, right, bottom)).save(output, "JPEG", quality=95, subsampling=0)


def create_template(project: Path, source_path: Path, topology_path: Path, build_path: Path, out_dir: str = "reports/qa/cad-views", tolerance_mm: float = 1.0) -> dict[str, Any]:
    source, topology, build = load_json(source_path), load_json(topology_path), load_json(build_path)
    for kind, value in (("source-index", source), ("building-topology", topology), ("sketchup-build", build)):
        issues = validate_contract(project, kind, value)
        if issues:
            raise ValueError(f"{kind} is invalid: " + "; ".join(f"{x.code}@{x.location}" for x in issues))
    if len({source.get("project_id"), topology.get("project_id"), build.get("project_id")}) != 1:
        raise ValueError("QA upstream contracts belong to different projects")

    frames = {str(item["id"]): item for item in source.get("frames") or []}
    registrations = {str(item["source_view_id"]): item for item in topology.get("registrations") or []}
    model_views = [item for item in source.get("views") or [] if item.get("role") in MODEL_ROLES]
    if not model_views:
        raise ValueError("No model-driving source views exist")
    if set(registrations) != {str(item["id"]) for item in model_views}:
        raise ValueError("Confirmed topology must register every model-driving source view exactly once")

    cad_dir = ensure_inside(project, out_dir, "CAD QA view directory")
    views, blockers = [], []
    for view in model_views:
        view_id = str(view["id"])
        frame = frames.get(str(view.get("frame_id")))
        if not frame:
            raise ValueError(f"Source view {view_id} has no confirmed owning frame")
        frame_jpg = resolve(project, frame.get("preview_jpg"))
        if not frame_jpg.is_file():
            raise ValueError(f"Confirmed frame JPG is missing for {view_id}")
        output = cad_dir / f"{view_id}.jpg"
        crop_view(frame_jpg, frame["bounds"], view["bounds"], output)
        registration = registrations[view_id]
        views.append({
            "source_view_id": view_id,
            "qa_view": str(view["qa_view"]),
            "role": str(view["role"]),
            "source_bounds": view["bounds"],
            "cad_view": {"path": relative(project, output), "sha256": sha256_file(output)},
            "registration_id": str(registration["id"]),
            "visible_topology_ids": list(registration["visible_topology_ids"]),
            "status": "expectation_required",
            "silhouette": [],
            "orientation_anchors": [],
            "features": [],
        })
        blockers.append(f"{view_id}: independently trace the complete silhouette, asymmetric anchors, and every model-driving feature from CAD")

    result = {
        "schema": SCHEMA,
        "contract_id": f"{source['project_id']}-independent-qa-r1",
        "project_id": source["project_id"],
        "revision": 1,
        "status": "cad_expectations_required",
        "upstream": [artifact(project, "source-index", source_path), artifact(project, "building-topology", topology_path), artifact(project, "sketchup-build", build_path)],
        "derivation": {"id": "", "agent_role": "independent_cad_qa"},
        "tolerance_mm": float(tolerance_mm),
        "views": views,
        "outputs": {
            "qa_plan_path": "work/qa/independent-qa-plan.json",
            "model_evidence_path": "reports/qa/model-evidence.json",
            "model_view_dir": "reports/qa/model-views",
            "overlay_dir": "reports/qa/overlays",
            "machine_result_path": "reports/qa/machine-result.json",
            "review_path": "work/qa/independent-review.json",
            "contract_path": "work/contracts/independent-qa.json"
        },
        "blocking_reasons": blockers,
        "unresolved": []
    }
    issues = validate_schema_file("independent-qa-derivation.schema.json", result)
    if issues:
        raise ValueError("Generated QA derivation is invalid: " + "; ".join(f"{x.code}@{x.location}" for x in issues))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Create independent CAD QA expectation workspace.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--source-index", required=True, type=Path)
    parser.add_argument("--topology", required=True, type=Path)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cad-view-dir", default="reports/qa/cad-views")
    parser.add_argument("--tolerance-mm", type=float, default=1.0)
    args = parser.parse_args()
    project = args.project.resolve()
    output = ensure_inside(project, args.out, "QA derivation")
    try:
        result = create_template(project, resolve(project, args.source_index), resolve(project, args.topology), resolve(project, args.build), args.cad_view_dir, args.tolerance_mm)
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc)); return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "views": len(result["views"]), "blockers": len(result["blocking_reasons"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
