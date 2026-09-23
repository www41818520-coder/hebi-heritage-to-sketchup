#!/usr/bin/env python3
"""Create a source-index contract from an independently verified CAD reader package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


UNIT_CODES = {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}
MODEL_ROLES = {"plan", "roof_plan", "elevation", "section"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified(value: dict[str, Any]) -> bool:
    return value.get("verification_state") == "verified"


def resolve_evidence_path(project: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    project_candidate = project / path
    if project_candidate.exists():
        return project_candidate
    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate
    matching_indexes = [
        index
        for index, part in enumerate(path.parts)
        if part.casefold() == project.name.casefold()
    ]
    for index in reversed(matching_indexes):
        rebased = project.joinpath(*path.parts[index + 1 :])
        if rebased.exists():
            return rebased
    return project_candidate


def evidence_reference(project: Path, raw: Any) -> Any:
    path = resolve_evidence_path(project, raw)
    if path is None:
        return raw
    try:
        return path.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def evidence_hashes(project: Path, record: dict[str, Any], keys: tuple[str, ...]) -> dict[str, str]:
    output: dict[str, str] = {}
    for key in keys:
        raw = record.get(key)
        if not raw:
            continue
        path = resolve_evidence_path(project, raw)
        if path is not None and path.is_file():
            output[key] = sha256_file(path)
    return output


def create_source_index(
    project: Path,
    project_id: str,
    source_paths: list[Path],
    source_role: str,
    package: dict[str, Any],
    units: str | None,
    units_basis: str,
    required_roles: list[str],
) -> dict[str, Any]:
    project = project.resolve()
    blockers: list[str] = []
    if package.get("verification_state") != "verified":
        blockers.append("CAD reader package has not passed independent audit")
    independent_review = package.get("independent_review") or {}
    if independent_review.get("schema") != "cad_reader.independent_review.2026-08-05":
        blockers.append("independent CAD reader review evidence is missing")
    semantic_summary = package.get("semantic_summary") or {}
    if semantic_summary.get("status") != "verified":
        blockers.append("CAD semantic inventory has not been verified")
    sources = []
    for index, source_path in enumerate(source_paths, 1):
        source_path = source_path.resolve()
        if not source_path.is_file():
            blockers.append(f"missing source file: {source_path}")
            continue
        try:
            relative = source_path.relative_to(project).as_posix()
        except ValueError:
            relative = str(source_path)
        sources.append({
            "id": f"CAD{index:02d}",
            "path": relative,
            "role": source_role,
            "sha256": sha256_file(source_path),
        })

    unit_value = units or UNIT_CODES.get(int(package.get("units_code", 0) or 0))
    if unit_value not in {"mm", "cm", "m", "in", "ft"}:
        blockers.append("units are absent or unsupported")

    frames = []
    frame_ids = set()
    for raw in package.get("frames") or []:
        frame_id = str(raw.get("id") or "")
        if not frame_id:
            blockers.append("frame without stable id")
            continue
        frame_ids.add(frame_id)
        coverage = raw.get("render_coverage") or {}
        if not verified(raw):
            blockers.append(f"{frame_id}: frame is not independently verified")
        if coverage.get("status") != "verified":
            blockers.append(f"{frame_id}: render coverage is not verified")
        if coverage.get("vector_status") != "verified" or coverage.get("coverage_ratio") != 1.0:
            blockers.append(f"{frame_id}: entity-to-render vector coverage is incomplete")
        if coverage.get("missing_handles"):
            blockers.append(f"{frame_id}: source handles are missing from rendering")
        edge_review = coverage.get("edge_review") or {}
        if edge_review.get("status") != "verified" or edge_review.get("unexplained_handles"):
            blockers.append(f"{frame_id}: unexplained entities touch the frame crop edge")
        missing_classes = list(coverage.get("missing_entity_classes") or [])
        for check, passed in (coverage.get("checks") or {}).items():
            if passed is not True:
                missing_classes.append(str(check))
        frames.append({
            "id": frame_id,
            "source_id": "CAD01",
            "bounds": raw.get("bounds"),
            "complete": verified(raw) and coverage.get("status") == "verified" and coverage.get("vector_status") == "verified" and coverage.get("coverage_ratio") == 1.0 and not missing_classes,
            "verification_state": raw.get("verification_state"),
            "preview_jpg": evidence_reference(project, raw.get("preview_jpg")),
            "vector_master": evidence_reference(project, raw.get("vector_master")),
            "render_manifest": evidence_reference(project, raw.get("render_manifest")),
            "trace_bounds_svg": evidence_reference(project, raw.get("trace_bounds_svg")),
            "evidence_sha256": evidence_hashes(project, raw, ("preview_jpg", "vector_master", "render_manifest", "trace_bounds_svg")),
            "render_coverage": {
                "status": coverage.get("status"),
                "vector_status": coverage.get("vector_status"),
                "coverage_ratio": coverage.get("coverage_ratio"),
                "missing_handles": coverage.get("missing_handles") or [],
                "edge_review": coverage.get("edge_review") or {},
                "missing_entity_classes": sorted(set(missing_classes)),
                "entity_census": raw.get("entity_census") or {},
            },
        })

    raw_views = package.get("regions") or [
        view for frame in package.get("frames") or [] for view in frame.get("views") or []
    ]
    views = []
    present_roles: set[str] = set()
    reader_release = str(package.get("skill_release") or "")
    for raw in raw_views:
        role = str(raw.get("role") or "unknown")
        include = raw.get("include", True)
        if role in MODEL_ROLES and include:
            present_roles.add(role)
        checks = raw.get("reading_checks") or {}
        is_verified = verified(raw)
        complete = is_verified and all(
            checks.get(key) is True
            for key in ("full_frame", "not_clipped", "separated_drawing_type", "view_inventory_confirmed")
        )
        readable = is_verified and all(
            checks.get(key) is True
            for key in ("title_visible", "readable_annotations", "source_coverage_checked")
        )
        view_id = str(raw.get("id") or "")
        segmentation_method = str(raw.get("segmentation_method") or "legacy_verified_bounds")
        segmentation_confidence = str(raw.get("segmentation_confidence") or "medium")
        if not view_id:
            blockers.append("view without stable id")
        if raw.get("frame_id") not in frame_ids:
            blockers.append(f"{view_id or 'unknown view'}: invalid frame link")
        if role in MODEL_ROLES and include:
            if reader_release >= "2026-08-05":
                if segmentation_method.endswith("fallback"):
                    blockers.append(f"{view_id}: title-only segmentation fallback is not deliverable")
                if segmentation_confidence == "low" or raw.get("segmentation_state") == "blocked":
                    blockers.append(f"{view_id}: mixed-view segmentation remains ambiguous")
            if not complete:
                blockers.append(f"{view_id}: view is incomplete or not verified")
            if not readable:
                blockers.append(f"{view_id}: annotations or coverage are not verified")
            if not raw.get("qa_view"):
                blockers.append(f"{view_id}: qa_view is missing")
            semantic = raw.get("semantic_index") or {}
            if semantic.get("status") != "verified" or semantic.get("blocking_reasons"):
                blockers.append(f"{view_id}: semantic title/role/direction inventory is not verified")
            if not (raw.get("title_evidence") or {}).get("canonical"):
                blockers.append(f"{view_id}: canonical title evidence is missing")
            if not raw.get("source_entity_ids"):
                blockers.append(f"{view_id}: source entity IDs are missing")
            vector_coverage = raw.get("vector_coverage") or {}
            if vector_coverage.get("status") != "verified" or vector_coverage.get("missing_handles"):
                blockers.append(f"{view_id}: entity-to-render vector coverage is incomplete")
            edge_review = vector_coverage.get("edge_review") or {}
            if edge_review.get("status") != "verified" or edge_review.get("unexplained_handles"):
                blockers.append(f"{view_id}: unexplained entities touch the view crop edge")
        views.append({
            "id": view_id,
            "frame_id": raw.get("frame_id"),
            "role": role,
            "title": raw.get("title", ""),
            "title_evidence": raw.get("title_evidence") or {},
            "qa_view": raw.get("qa_view", ""),
            "semantic_index": raw.get("semantic_index") or {},
            "bounds": raw.get("bounds"),
            "segmentation_method": segmentation_method,
            "segmentation_confidence": segmentation_confidence,
            "segmentation_evidence": raw.get("segmentation_evidence") or {},
            "include": include,
            "complete": complete,
            "readable": readable,
            "source_entity_ids": raw.get("source_entity_ids") or [],
            "render_manifest": evidence_reference(project, raw.get("render_manifest")),
            "trace_bounds_svg": evidence_reference(project, raw.get("trace_bounds_svg")),
            "vector_coverage": raw.get("vector_coverage") or {},
            "evidence_sha256": evidence_hashes(project, raw, ("render_manifest", "trace_bounds_svg")),
        })

    missing_roles = sorted(set(required_roles) - present_roles)
    if missing_roles:
        blockers.append(f"required model-driving roles are absent: {missing_roles}")
    if blockers:
        raise ValueError("Source index remains blocked:\n- " + "\n- ".join(blockers))

    return {
        "schema": "cad_to_sketchup.source_index.2026-08-05",
        "contract_id": f"{project_id}-source-index-r1",
        "project_id": project_id,
        "revision": 1,
        "status": "verified",
        "upstream": [],
        "units": {"value": unit_value, "basis": units_basis},
        "sources": sources,
        "frames": frames,
        "views": views,
        "semantic_summary": semantic_summary,
        "reader_audit": independent_review,
        "required_roles": required_roles,
        "unresolved": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a verified source-index contract.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--source", required=True, action="append", type=Path)
    parser.add_argument("--source-role", default="combined_architectural_set")
    parser.add_argument("--regions", required=True, type=Path)
    parser.add_argument("--units", choices=("mm", "cm", "m", "in", "ft"))
    parser.add_argument("--units-basis", choices=("cad_header", "user_confirmed"), default="cad_header")
    parser.add_argument("--required-roles", required=True, help="Comma-separated roles, for example plan,elevation,section")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    try:
        result = create_source_index(
            args.project,
            args.project_id,
            args.source,
            args.source_role,
            load_json(args.regions),
            args.units,
            args.units_basis,
            [item.strip() for item in args.required_roles.split(",") if item.strip()],
        )
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc))
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "frames": len(result["frames"]), "views": len(result["views"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
