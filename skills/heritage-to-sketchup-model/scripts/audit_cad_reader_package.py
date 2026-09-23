#!/usr/bin/env python3
"""Independently promote a CAD reader candidate package to verified evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


REVIEW_SCHEMA = "cad_reader.independent_review.2026-08-05"
VISUAL_FRAME_CHECKS = ("full_border_visible", "readable_at_review_resolution", "not_clipped")
VISUAL_VIEW_CHECKS = (
    "full_frame",
    "title_visible",
    "readable_annotations",
    "axis_bubbles_visible",
    "not_clipped",
    "separated_drawing_type",
    "source_coverage_checked",
    "view_inventory_confirmed",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(project: Path, raw: Any) -> Path | None:
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

    # Reader packages may be generated from a repository root while --project
    # points at a nested test project. Rebase the suffix after that project
    # directory instead of duplicating the whole repository-relative path.
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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def create_review_template(package: dict[str, Any], package_sha256: str) -> dict[str, Any]:
    return {
        "schema": REVIEW_SCHEMA,
        "source_package_sha256": package_sha256,
        "reviewer": {
            "type": "independent_agent_or_human",
            "id": "",
            "derivation_id": "",
        },
        "frames": {
            str(frame.get("id")): {key: False for key in VISUAL_FRAME_CHECKS}
            for frame in package.get("frames") or []
        },
        "views": {
            str(view.get("id")): {key: False for key in VISUAL_VIEW_CHECKS}
            for view in package.get("regions") or []
        },
        "edge_explanations": {
            str(record.get("id")): [
                {"handle": handle, "reason": ""}
                for handle in (record.get("vector_coverage") or record.get("render_coverage") or {})
                .get("edge_review", {})
                .get("unexplained_handles", [])
            ]
            for record in [*(package.get("frames") or []), *(package.get("regions") or [])]
        },
        "notes": [],
    }


def _reviewed_edges(record: dict[str, Any], review: dict[str, Any]) -> tuple[list[str], list[str], list[dict[str, str]]]:
    coverage = record.get("vector_coverage") or record.get("render_coverage") or {}
    edge_review = coverage.get("edge_review") or {}
    existing_explained = set(edge_review.get("explained_handles") or [])
    unexplained = set(edge_review.get("unexplained_handles") or [])
    supplied = review.get("edge_explanations", {}).get(str(record.get("id")), [])
    explanations = list(edge_review.get("explanations") or [])
    for item in supplied:
        handle = str(item.get("handle") or "")
        reason = str(item.get("reason") or "").strip()
        if handle in unexplained and reason:
            unexplained.remove(handle)
            existing_explained.add(handle)
            explanations.append({"handle": handle, "reason": reason, "basis": "independent_review"})
    return sorted(existing_explained), sorted(unexplained), explanations


def audit_package(
    project: Path,
    package: dict[str, Any],
    review: dict[str, Any],
    package_sha256: str,
) -> tuple[dict[str, Any], list[str]]:
    project = project.resolve()
    result = copy.deepcopy(package)
    review_sha256 = hashlib.sha256(
        json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    issues: list[str] = []
    if review.get("schema") != REVIEW_SCHEMA:
        issues.append("review schema is invalid")
    if review.get("source_package_sha256") != package_sha256:
        issues.append("review is stale: source package hash changed")
    reviewer = review.get("reviewer") or {}
    if not reviewer.get("id") or not reviewer.get("derivation_id"):
        issues.append("independent reviewer identity and derivation_id are required")
    if reviewer.get("derivation_id") == package.get("render_engine"):
        issues.append("review derivation must differ from the CAD render engine")

    for frame in result.get("frames") or []:
        frame_id = str(frame.get("id") or "UNKNOWN")
        manifest_path = resolve_path(project, frame.get("render_manifest"))
        preview_path = resolve_path(project, frame.get("preview_jpg"))
        vector_path = resolve_path(project, frame.get("vector_master"))
        if manifest_path is None or not manifest_path.is_file():
            issues.append(f"{frame_id}: render manifest is missing")
            manifest = {}
        else:
            manifest = load_json(manifest_path)
        if preview_path is None or not preview_path.is_file():
            issues.append(f"{frame_id}: preview JPG is missing")
        if vector_path is None or not vector_path.is_file():
            issues.append(f"{frame_id}: vector master is missing")
        if manifest.get("coverage_ratio") != 1.0 or manifest.get("missing_handles"):
            issues.append(f"{frame_id}: source-to-render coverage is incomplete")
        checks = review.get("frames", {}).get(frame_id, {})
        for key in VISUAL_FRAME_CHECKS:
            if checks.get(key) is not True:
                issues.append(f"{frame_id}: visual check {key} is not verified")
        explained, unexplained, explanations = _reviewed_edges(frame, review)
        if unexplained:
            issues.append(f"{frame_id}: unexplained frame-edge handles remain: {unexplained}")
        coverage = frame.get("render_coverage") or {}
        coverage["edge_review"] = {
            "status": "verified" if not unexplained else "needs_verification",
            "explained_handles": explained,
            "unexplained_handles": unexplained,
            "explanations": explanations,
        }
        coverage["checks"] = {**(coverage.get("checks") or {}), **checks}
        frame["render_coverage"] = coverage

    for view in result.get("regions") or []:
        view_id = str(view.get("id") or "UNKNOWN")
        manifest_path = resolve_path(project, view.get("render_manifest"))
        if manifest_path is None or not manifest_path.is_file():
            issues.append(f"{view_id}: view manifest is missing")
            manifest = {}
        else:
            manifest = load_json(manifest_path)
        if manifest.get("coverage_ratio") != 1.0 or manifest.get("missing_handles"):
            issues.append(f"{view_id}: view render coverage is incomplete")
        if view.get("segmentation_confidence") == "low" or view.get("segmentation_state") == "blocked":
            issues.append(f"{view_id}: segmentation remains ambiguous")
        semantic = view.get("semantic_index") or {}
        if semantic.get("status") == "blocked" or semantic.get("blocking_reasons"):
            issues.append(f"{view_id}: semantic index remains blocked")
        if not view.get("qa_view"):
            issues.append(f"{view_id}: qa_view is missing")
        checks = review.get("views", {}).get(view_id, {})
        for key in VISUAL_VIEW_CHECKS:
            if checks.get(key) is not True:
                issues.append(f"{view_id}: visual check {key} is not verified")
        explained, unexplained, explanations = _reviewed_edges(view, review)
        if unexplained:
            issues.append(f"{view_id}: unexplained view-edge handles remain: {unexplained}")
        coverage = view.get("vector_coverage") or {}
        coverage["edge_review"] = {
            "status": "verified" if not unexplained else "needs_verification",
            "explained_handles": explained,
            "unexplained_handles": unexplained,
            "explanations": explanations,
        }
        view["vector_coverage"] = coverage
        view["reading_checks"] = checks

    summary = result.get("semantic_summary") or {}
    if summary.get("status") == "blocked" or summary.get("duplicate_qa_view_keys"):
        issues.append("package semantic summary remains blocked")

    if not issues:
        for frame in result.get("frames") or []:
            frame["verification_state"] = "verified"
            frame["render_coverage"]["status"] = "verified"
            frame["blocking_reasons"] = []
        for view in result.get("regions") or []:
            view["verification_state"] = "verified"
            view["confirmation_state"] = "confirmed"
            view["segmentation_state"] = "verified"
            view["semantic_index"]["status"] = "verified"
            view["vector_coverage"]["status"] = "verified"
            view["blocking_reasons"] = []
        for frame in result.get("frames") or []:
            frame["views"] = [view for view in result.get("regions") or [] if view.get("frame_id") == frame.get("id")]
        result["semantic_summary"]["status"] = "verified"
        result["verification_required"] = False
        result["verification_state"] = "verified"
        result["independent_review"] = {
            "schema": REVIEW_SCHEMA,
            "reviewer": reviewer,
            "source_package_sha256": package_sha256,
            "review_sha256": review_sha256,
        }
    else:
        result["verification_state"] = "blocked"
    result["audit"] = {"status": "verified" if not issues else "blocked", "issues": issues}
    return result, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and promote a CAD reader package.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--review-template", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    args.project = args.project.resolve()
    for key in ("package", "review", "review_template", "out"):
        value = getattr(args, key)
        if value is not None and not value.is_absolute():
            setattr(args, key, args.project / value)

    package = load_json(args.package)
    package_hash = sha256_file(args.package)
    if args.review is None:
        if args.review_template is None:
            parser.error("--review-template is required when --review is absent")
        template = create_review_template(package, package_hash)
        args.review_template.parent.mkdir(parents=True, exist_ok=True)
        args.review_template.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "needs_independent_review", "template": str(args.review_template)}, ensure_ascii=False))
        return 2
    if args.out is None:
        parser.error("--out is required with --review")
    from review_session import require_receipt
    require_receipt(args.project, args.review, "reading")
    result, issues = audit_package(args.project, package, load_json(args.review), package_hash)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["audit"]["status"], "issues": len(issues), "out": str(args.out)}, ensure_ascii=False))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
