#!/usr/bin/env python3
"""Compile independently traced CAD expectations into a read-only model-export plan."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from contract_validation import load_json, validate_schema_file
from create_independent_qa_derivation import SCHEMA as DERIVATION_SCHEMA
from independent_qa_common import assert_hashed_artifact, ensure_inside, relative, resolve, sha256_file, valid_bounds


PLAN_SCHEMA = "cad_to_sketchup.independent_qa_plan.2026-08-06"


def _inside(inner: dict[str, Any], outer: dict[str, Any], tolerance: float = 1e-6) -> bool:
    return valid_bounds(inner) and all(float(inner[key]) >= float(outer[key]) - tolerance for key in ("xmin", "ymin")) and all(float(inner[key]) <= float(outer[key]) + tolerance for key in ("xmax", "ymax"))


def compile_plan(project: Path, derivation_path: Path, derivation: dict[str, Any]) -> dict[str, Any]:
    blockers = [f"{x.code}@{x.location}: {x.message}" for x in validate_schema_file("independent-qa-derivation.schema.json", derivation)]
    if derivation.get("schema") != DERIVATION_SCHEMA or derivation.get("status") != "ready_for_compile":
        blockers.append("QA derivation must have status ready_for_compile")
    if derivation.get("blocking_reasons") or derivation.get("unresolved"):
        blockers.append("QA derivation still contains blockers or unresolved items")
    cad_id = str((derivation.get("derivation") or {}).get("id") or "")
    if not cad_id:
        blockers.append("Independent CAD derivation ID is missing")

    upstream = {str(item.get("kind")): item for item in derivation.get("upstream") or []}
    loaded: dict[str, dict[str, Any]] = {}
    for kind in ("source-index", "building-topology", "sketchup-build"):
        try:
            path = assert_hashed_artifact(project, upstream.get(kind) or {}, kind)
            loaded[kind] = load_json(path)
        except (OSError, ValueError, TypeError) as exc:
            blockers.append(str(exc))
    source, topology, build = (loaded.get(key) or {} for key in ("source-index", "building-topology", "sketchup-build"))

    source_views = {str(item["id"]): item for item in source.get("views") or [] if item.get("role") in {"plan", "roof_plan", "elevation", "section"}}
    registrations = {str(item["source_view_id"]): item for item in topology.get("registrations") or []}
    views = derivation.get("views") or []
    ids = [str(item.get("source_view_id") or "") for item in views]
    if len(ids) != len(set(ids)) or set(ids) != set(source_views):
        blockers.append("QA views must cover every model-driving source view exactly once")
    elevation_count = sum(1 for item in views if item.get("role") == "elevation")
    if elevation_count < 4:
        blockers.append(f"Full delivery requires four distinct elevation views; found {elevation_count}")
    level_count = len(topology.get("levels") or [])
    plan_count = sum(1 for item in views if item.get("role") == "plan")
    if plan_count < level_count:
        blockers.append(f"Every modeled floor requires a plan QA view; floors={level_count} plans={plan_count}")
    if topology.get("roofs") and not any(item.get("role") == "roof_plan" for item in views):
        blockers.append("A modeled roof requires a roof-plan QA view")

    for index, view in enumerate(views):
        view_id = str(view.get("source_view_id") or "")
        source_view = source_views.get(view_id) or {}
        registration = registrations.get(view_id) or {}
        if view.get("status") != "verified":
            blockers.append(f"{view_id}: CAD expectations are not independently verified")
        if view.get("role") != source_view.get("role") or view.get("qa_view") != source_view.get("qa_view"):
            blockers.append(f"{view_id}: role or QA key drifted from source-index")
        if view.get("registration_id") != registration.get("id") or set(view.get("visible_topology_ids") or []) != set(registration.get("visible_topology_ids") or []):
            blockers.append(f"{view_id}: visible topology scope drifted from confirmed registration")
        if len(view.get("silhouette") or []) < 3:
            blockers.append(f"{view_id}: complete CAD silhouette is required")
        if view.get("role") == "elevation" and len(view.get("orientation_anchors") or []) < 2:
            blockers.append(f"{view_id}: two asymmetric orientation anchors are required")
        features = view.get("features") or []
        if not features:
            blockers.append(f"{view_id}: no independently traced model-driving CAD features")
        feature_ids = [str(item.get("id") or "") for item in features]
        if len(feature_ids) != len(set(feature_ids)):
            blockers.append(f"{view_id}: duplicate feature IDs")
        for feature in features:
            if not _inside(feature.get("bounds") or {}, view.get("source_bounds") or {}):
                blockers.append(f"{view_id}/{feature.get('id')}: feature bounds lie outside the complete source view")
        try:
            assert_hashed_artifact(project, view.get("cad_view") or {}, f"CAD view {view_id}")
        except ValueError as exc:
            blockers.append(str(exc))

    other_ids = {
        str(((source.get("reader_audit") or {}).get("reviewer") or {}).get("derivation_id") or ""),
        str((topology.get("derivation") or {}).get("id") or ""),
    }
    build_plan_ref = build.get("build_plan") or {}
    build_plan_path = None
    try:
        build_plan_path = assert_hashed_artifact(project, build_plan_ref, "production build plan")
        build_plan = load_json(build_plan_path)
        other_ids.add(str((build_plan.get("derivation") or {}).get("id") or ""))
    except (OSError, ValueError, TypeError) as exc:
        blockers.append(str(exc))
    if cad_id in other_ids:
        blockers.append("Independent CAD QA derivation must differ from reader, topology, and production derivations")

    active = build.get("active_model") or {}
    try:
        active_path = assert_hashed_artifact(project, active, "active production SKP")
    except ValueError as exc:
        blockers.append(str(exc)); active_path = resolve(project, active.get("path"))
    if blockers:
        raise ValueError("Independent QA plan remains blocked:\n- " + "\n- ".join(blockers))

    plan = {
        "schema": PLAN_SCHEMA,
        "contract_id": derivation["contract_id"],
        "project_id": derivation["project_id"],
        "revision": derivation["revision"],
        "status": "ready_for_model_export",
        "execution_allowed": True,
        "project_root": str(project.resolve()),
        "upstream": copy.deepcopy(derivation["upstream"]) + [{"kind": "independent-qa-derivation", "path": relative(project, derivation_path), "sha256": sha256_file(derivation_path)}],
        "derivation": copy.deepcopy(derivation["derivation"]),
        "tolerance_mm": derivation["tolerance_mm"],
        "active_model": {"path": relative(project, active_path), "sha256": sha256_file(active_path), "production_root": str((build.get("generator") or {}).get("root_group"))},
        "views": copy.deepcopy(views),
        "outputs": copy.deepcopy(derivation["outputs"]),
        "unresolved": []
    }
    issues = validate_schema_file("independent-qa-plan.schema.json", plan)
    if issues:
        raise ValueError("Compiled independent QA plan is invalid: " + "; ".join(f"{x.code}@{x.location}" for x in issues))
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile independent QA plan.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--derivation", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(); project = args.project.resolve()
    derivation_path = resolve(project, args.derivation); output = ensure_inside(project, args.out, "QA plan")
    try:
        plan = compile_plan(project, derivation_path, load_json(derivation_path))
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc)); return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": plan["status"], "views": len(plan["views"])}, ensure_ascii=False)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
