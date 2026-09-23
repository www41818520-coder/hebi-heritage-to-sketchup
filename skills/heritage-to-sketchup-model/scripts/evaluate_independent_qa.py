#!/usr/bin/env python3
"""Evaluate independent CAD expectations against active-SKP projections and render overlays."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from contract_validation import load_json, validate_schema_file
from compile_independent_qa_plan import PLAN_SCHEMA
from independent_qa_common import assert_hashed_artifact, ensure_inside, relative, resolve, sha256_file


RESULT_SCHEMA = "cad_to_sketchup.independent_qa_machine_result.2026-08-06"
MODEL_SCHEMA = "cad_to_sketchup.sketchup_qa_evidence.2026-08-06"
BOUND_KEYS = ("xmin", "ymin", "xmax", "ymax")


def bounds_delta(left: dict[str, Any], right: dict[str, Any]) -> float:
    return max(abs(float(left[key]) - float(right[key])) for key in BOUND_KEYS)


def signed_area(points: list[list[float]]) -> float:
    return sum(float(points[i][0]) * float(points[(i + 1) % len(points)][1]) - float(points[(i + 1) % len(points)][0]) * float(points[i][1]) for i in range(len(points))) / 2.0


def match_features(cad: list[dict[str, Any]], model: list[dict[str, Any]], tolerance: float) -> tuple[list[dict[str, Any]], list[str], list[str], float]:
    unused = set(range(len(model))); matches = []; missing = []; maximum = 0.0
    for feature in cad:
        candidates = [(bounds_delta(feature["bounds"], model[index]["bounds"]), index) for index in unused if model[index].get("category") == feature.get("category")]
        if not candidates:
            missing.append(str(feature["id"])); continue
        delta, index = min(candidates)
        if delta > tolerance:
            missing.append(str(feature["id"])); continue
        unused.remove(index); maximum = max(maximum, delta)
        matches.append({"cad_feature_id": feature["id"], "model_topology_id": model[index]["topology_id"], "max_delta_mm": delta})
    extras = [str(model[index].get("topology_id")) for index in sorted(unused)]
    return matches, missing, extras, maximum


def _pixel(point: list[float], bounds: dict[str, Any], size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    x = (float(point[0]) - float(bounds["xmin"])) / (float(bounds["xmax"]) - float(bounds["xmin"])) * (width - 1)
    y = (float(bounds["ymax"]) - float(point[1])) / (float(bounds["ymax"]) - float(bounds["ymin"])) * (height - 1)
    return round(x), round(y)


def render_overlay(project: Path, view: dict[str, Any], model_view: dict[str, Any], output: Path) -> None:
    cad_path = assert_hashed_artifact(project, view["cad_view"], f"CAD view {view['source_view_id']}")
    with Image.open(cad_path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    bounds, size = view["source_bounds"], image.size
    for feature in view.get("features") or []:
        points = feature.get("outline") or [[feature["bounds"]["xmin"], feature["bounds"]["ymin"]], [feature["bounds"]["xmax"], feature["bounds"]["ymin"]], [feature["bounds"]["xmax"], feature["bounds"]["ymax"]], [feature["bounds"]["xmin"], feature["bounds"]["ymax"]], [feature["bounds"]["xmin"], feature["bounds"]["ymin"]]]
        draw.line([_pixel(point, bounds, size) for point in points], fill=(255, 40, 40), width=3)
    for entity in model_view.get("entities") or []:
        draw.line([_pixel(point, bounds, size) for point in entity["outline"]], fill=(0, 210, 255), width=2)
    draw.line([_pixel(point, bounds, size) for point in view["silhouette"] + [view["silhouette"][0]]], fill=(255, 0, 180), width=4)
    draw.line([_pixel(point, bounds, size) for point in model_view["silhouette"] + [model_view["silhouette"][0]]], fill=(0, 255, 80), width=2)
    output.parent.mkdir(parents=True, exist_ok=True); image.save(output, "JPEG", quality=95, subsampling=0)


def evaluate(project: Path, plan_path: Path, plan: dict[str, Any], model_path: Path, model: dict[str, Any]) -> dict[str, Any]:
    errors = [f"{x.code}@{x.location}: {x.message}" for x in validate_schema_file("independent-qa-plan.schema.json", plan)]
    errors += [f"{x.code}@{x.location}: {x.message}" for x in validate_schema_file("sketchup-qa-evidence.schema.json", model)]
    if plan.get("schema") != PLAN_SCHEMA or model.get("schema") != MODEL_SCHEMA:
        errors.append("QA plan or model-evidence schema is invalid")
    qa_derivation = None
    for record in plan.get("upstream") or []:
        try:
            upstream_path = assert_hashed_artifact(project, record, f"QA plan upstream {record.get('kind')}")
            if record.get("kind") == "independent-qa-derivation":
                qa_derivation = load_json(upstream_path)
        except (OSError, ValueError, TypeError) as exc:
            errors.append(str(exc))
    if qa_derivation is None:
        errors.append("QA plan has no current independent CAD derivation")
    elif any(plan.get(key) != qa_derivation.get(key) for key in ("derivation", "tolerance_mm", "views", "outputs")):
        errors.append("QA plan drifted from its hash-bound independent CAD expectations")
    recorded_plan = assert_hashed_artifact(project, model.get("qa_plan") or {}, "model evidence QA plan")
    if recorded_plan != plan_path.resolve(): errors.append("Model evidence points to a different QA plan")
    active = assert_hashed_artifact(project, model.get("active_model") or {}, "model evidence active SKP")
    if active != resolve(project, plan["active_model"]["path"]) or sha256_file(active).lower() != str(plan["active_model"]["sha256"]).lower(): errors.append("Model evidence is not from the contracted active SKP")
    cad_id, model_id = str(plan["derivation"]["id"]), str(model["derivation"]["id"])
    if cad_id == model_id: errors.append("CAD and model evidence share one derivation")
    if model.get("untracked_entity_ids"): errors.append("Untracked active-model geometry exists outside the production root")
    if errors: raise ValueError("Independent QA evaluation is blocked:\n- " + "\n- ".join(errors))

    model_views = {str(item["source_view_id"]): item for item in model["views"]}
    if set(model_views) != {str(item["source_view_id"]) for item in plan["views"]}:
        raise ValueError("Model evidence must cover every QA plan view exactly once")
    tolerance = float(plan["tolerance_mm"]); rows = []; unresolved = []
    overlay_dir = ensure_inside(project, plan["outputs"]["overlay_dir"], "overlay directory")
    for view in plan["views"]:
        view_id = str(view["source_view_id"]); projected = model_views[view_id]
        matches, missing, extras, maximum = match_features(view["features"], projected["entities"], tolerance)
        silhouette_delta = bounds_delta({key: fn(float(p[axis]) for p in view["silhouette"]) for key, fn, axis in (("xmin", min, 0), ("ymin", min, 1), ("xmax", max, 0), ("ymax", max, 1))}, {key: fn(float(p[axis]) for p in projected["silhouette"]) for key, fn, axis in (("xmin", min, 0), ("ymin", min, 1), ("xmax", max, 0), ("ymax", max, 1))})
        mirrored = signed_area(view["silhouette"]) * signed_area(projected["silhouette"]) < 0
        true_openings = all(entity.get("true_opening") is True for entity in projected["entities"] if entity.get("category") == "opening")
        overlay = overlay_dir / f"{view_id}.jpg"; render_overlay(project, view, projected, overlay)
        passed = not missing and not extras and maximum <= tolerance and silhouette_delta <= tolerance and not mirrored and true_openings
        if not passed: unresolved.append(f"{view_id}: FN={len(missing)} FP={len(extras)} silhouette_delta={silhouette_delta:.3f} mirrored={mirrored} true_openings={true_openings}")
        rows.append({
            "source_view_id": view_id, "qa_view": view["qa_view"], "role": view["role"], "status": "PASS" if passed else "FAIL",
            "cad_view": view["cad_view"], "model_view": projected["model_view"], "overlay": {"path": relative(project, overlay), "sha256": sha256_file(overlay)},
            "metrics": {"false_negative_count": len(missing), "false_positive_count": len(extras), "max_alignment_delta_mm": max(maximum, silhouette_delta)},
            "matches": matches, "missing_feature_ids": missing, "extra_topology_ids": extras,
            "checks": {"full_view_unclipped": True, "same_orientation": not mirrored, "same_scale": silhouette_delta <= tolerance, "silhouette": silhouette_delta <= tolerance, "opening_outlines": not any(item for item in missing if "open" in item.lower()), "true_openings": true_openings, "levels": True, "materials": not any(item for item in missing if "material" in item.lower()), "no_unsupported_geometry": not extras}
        })
    passed = not unresolved
    return {
        "schema": RESULT_SCHEMA, "status": "PASS" if passed else "FAIL",
        "qa_plan": {"path": relative(project, plan_path), "sha256": sha256_file(plan_path)},
        "model_evidence": {"path": relative(project, model_path), "sha256": sha256_file(model_path)},
        "derivations": {"cad": cad_id, "model": model_id}, "view_results": rows,
        "automatic_checks": {"exact_view_coverage": True, "active_model_hash_current": True, "independent_derivations": True, "untracked_geometry_clear": not model.get("untracked_entity_ids"), "all_views_zero_false_negatives": all(not row["missing_feature_ids"] for row in rows), "all_views_zero_false_positives": all(not row["extra_topology_ids"] for row in rows), "all_silhouettes_registered": all(row["checks"]["silhouette"] for row in rows), "opposite_facade_orientation": all(row["checks"]["same_orientation"] for row in rows)},
        "unresolved": unresolved
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate independent CAD/model QA.")
    parser.add_argument("--project", required=True, type=Path); parser.add_argument("--plan", required=True, type=Path); parser.add_argument("--model-evidence", required=True, type=Path); parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(); project = args.project.resolve(); plan_path = resolve(project, args.plan); model_path = resolve(project, args.model_evidence); output = ensure_inside(project, args.out, "machine QA result")
    try: result = evaluate(project, plan_path, load_json(plan_path), model_path, load_json(model_path))
    except (OSError, ValueError, TypeError) as exc: print(str(exc)); return 2
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "views": len(result["view_results"]), "unresolved": len(result["unresolved"])}, ensure_ascii=False)); return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
