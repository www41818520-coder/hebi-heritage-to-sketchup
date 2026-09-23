#!/usr/bin/env python3
"""Create a complete, non-executable topology-derivation workspace template."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from contract_validation import MODEL_ROLES, load_json, validate_contract, validate_schema_file


SCHEMA = "cad_to_sketchup.topology_derivation.2026-08-05"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(project: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def axis_rank(value: str) -> tuple[int, int | str]:
    token = value.strip().upper()
    if token.isdigit():
        return 0, int(token)
    return 1, token


def direction_tokens(view: dict[str, Any]) -> tuple[str, str]:
    direction = (view.get("semantic_index") or {}).get("direction") or {}
    start = str(direction.get("from_axis") or "")
    end = str(direction.get("to_axis") or "")
    if start and end:
        return start, end
    key = str(direction.get("key") or "")
    match = re.match(r"^(.+?)_to_(.+)$", key, re.I)
    return (match.group(1), match.group(2)) if match else ("", "")


def suggested_axes(view: dict[str, Any]) -> tuple[list[float], list[float], str]:
    role = view.get("role")
    if role in {"plan", "roof_plan"}:
        return [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], "plan_xy"
    if role == "elevation":
        start, end = direction_tokens(view)
        if start and end:
            start_rank = axis_rank(start)
            end_rank = axis_rank(end)
            sign = 1.0 if start_rank < end_rank else -1.0
            if start_rank[0] == end_rank[0] == 0:
                return [sign, 0.0, 0.0], [0.0, 0.0, 1.0], "numeric_axis_facade_candidate"
            if start_rank[0] == end_rank[0] == 1:
                return [0.0, sign, 0.0], [0.0, 0.0, 1.0], "letter_axis_facade_candidate"
    return [1.0, 0.0, 0.0], [0.0, 0.0, 1.0], "requires_section_or_facade_registration"


def anchor_ids(view: dict[str, Any]) -> list[str]:
    semantic = view.get("semantic_index") or {}
    axis_ids = list((semantic.get("axis") or {}).get("evidence_text_ids") or [])
    level_ids = [str(item.get("evidence_text_id")) for item in semantic.get("levels") or [] if item.get("evidence_text_id")]
    candidates = axis_ids + level_ids + list(view.get("source_entity_ids") or [])
    unique: list[str] = []
    for item in candidates:
        value = str(item)
        if value and value not in unique:
            unique.append(value)
    return unique[:2]


def evidence_kind(role: str) -> str:
    result = {
        "plan": "measured_plan",
        "roof_plan": "measured_plan",
        "elevation": "measured_elevation",
        "section": "measured_section",
    }.get(role, "measured_plan")


def create_template(project: Path, source_path: Path, source: dict[str, Any]) -> dict[str, Any]:
    issues = validate_contract(project, "source-index", source)
    if issues:
        raise ValueError("; ".join(f"{item.code}@{item.location}: {item.message}" for item in issues))
    views = [
        view for view in source.get("views") or []
        if view.get("role") in MODEL_ROLES and view.get("include", True)
    ]
    registrations = []
    blockers = []
    for view in views:
        source_view_id = str(view.get("id"))
        role = str(view.get("role"))
        x_axis, y_axis, basis = suggested_axes(view)
        ids = anchor_ids(view)
        anchors = [
            {
                "source_view_id": source_view_id,
                "kind": evidence_kind(role),
                "source_entity_ids": [item],
            }
            for item in ids
        ]
        reason = "verify two asymmetric anchors and set the exact project origin"
        if role in {"elevation", "section"}:
            reason += "; verify camera/outward side or section cut plane from plan evidence"
        blockers.append(f"registration:{source_view_id}:{reason}")
        registrations.append(
            {
                "id": f"REG-{source_view_id}",
                "source_view_id": source_view_id,
                "qa_view": view.get("qa_view"),
                "role": role,
                "transform": {
                    "source_origin": [float((view.get("bounds") or {}).get("xmin", 0.0)), float((view.get("bounds") or {}).get("ymin", 0.0))],
                    "origin": [0.0, 0.0, 0.0],
                    "x_axis": x_axis,
                    "y_axis": y_axis,
                    "scale": 1.0,
                },
                "visible_topology_ids": [],
                "anchor_evidence": anchors,
                "status": "candidate",
                "suggestion_basis": basis,
                "review_note": reason,
            }
        )
    blockers.extend(
        [
            "topology:derive every true level footprint and elevation range",
            "topology:derive ordered exterior wall rings and measured thicknesses",
            "topology:derive slabs, roofs, openings, curtain walls, sweeps, and canopies when present",
            "topology:bind every object to CAD source-view and entity evidence",
        ]
    )
    source_hash = sha256_file(source_path)
    return {
        "schema": SCHEMA,
        "contract_id": f"{source.get('project_id')}-topology-r1",
        "project_id": source.get("project_id"),
        "revision": 1,
        "status": "derivation_required",
        "upstream": [{"kind": "source-index", "path": relative(project, source_path), "sha256": source_hash}],
        "derivation": {"id": "", "agent_role": "topology_derivation"},
        "coordinate_system": {
            "units": "mm",
            "origin": [0.0, 0.0, 0.0],
            "x_axis": [1.0, 0.0, 0.0],
            "y_axis": [0.0, 1.0, 0.0],
            "z_axis": [0.0, 0.0, 1.0],
            "elevation_datum": "architectural_0.000",
        },
        "registrations": registrations,
        "control_basis": {
            "priority": ["user_confirmation", "control_mass", "cad_measurement", "documented_default"],
            "control_mass": {"provided": False, "scope": "none"},
        },
        "levels": [],
        "walls": [],
        "internal_walls": [],
        "slabs": [],
        "roofs": [],
        "roof_lights": [],
        "openings": [],
        "curtain_walls": [],
        "sweeps": [],
        "canopies": [],
        "materials": [],
        "white_model_output": {
            "model_path": "work/topology/topology-white-model.skp",
            "result_path": "work/topology/white-model-result.json",
            "review_dir": "work/topology/review-views",
        },
        "blocking_reasons": blockers,
        "unresolved": [],
    }
    schema_issues = validate_schema_file("topology-derivation.schema.json", result)
    if schema_issues:
        raise ValueError("; ".join(f"{item.code}@{item.location}: {item.message}" for item in schema_issues))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a topology-derivation workspace from a verified source index.")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    source_path = args.source_index if args.source_index.is_absolute() else project / args.source_index
    output = args.out if args.out.is_absolute() else project / args.out
    try:
        result = create_template(project, source_path, load_json(source_path))
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc))
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "registrations": len(result["registrations"]), "blockers": len(result["blocking_reasons"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
