#!/usr/bin/env python3
"""Create an independent topology review template and promote only a passed candidate."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from contract_validation import TOPOLOGY_AUDIT_CHECKS, validate_contract


CANDIDATE_SCHEMA = "cad_to_sketchup.building_topology_candidate.2026-08-05"
REVIEW_SCHEMA = "cad_to_sketchup.topology_review.2026-08-05"
FINAL_SCHEMA = "cad_to_sketchup.building_topology.2026-08-05"
VIEW_CHECKS = (
    "registration_correct",
    "silhouette_complete",
    "object_hosting_correct",
    "cross_view_consistent",
)
BOUNDARY_KEYS = (
    "envelope_and_levels",
    "wall_roof_closure",
    "slabs_and_roofs",
    "openings",
    "curtain_walls",
    "sweeps_and_cornices",
    "canopies",
    "entrance_steps_and_ramps",
    "material_zone_boundaries",
    "interior_wall_terminations",
)
ALLOWED_TO_IGNORE = (
    "exact_material_appearance",
    "glass_optics",
    "fine_frame_hardware",
    "rendering_and_entourage",
)
CORE_CONTRACT_KEYS = (
    "contract_id", "project_id", "revision", "upstream", "derivation",
    "coordinate_system", "registrations", "control_basis", "levels", "walls", "internal_walls",
    "slabs", "roofs", "roof_lights", "openings", "curtain_walls", "sweeps", "canopies",
    "materials", "unresolved", "access_elements", "columns",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(project: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return path if path.is_absolute() else project / path


def project_relative(project: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def boundary_candidate_counts(candidate: dict[str, Any]) -> dict[str, int]:
    return {
        "envelope_and_levels": len(candidate.get("levels") or []) + len(candidate.get("walls") or []),
        "wall_roof_closure": len(candidate.get("walls") or []),
        "slabs_and_roofs": len(candidate.get("slabs") or []) + len(candidate.get("roofs") or []) + len(candidate.get("roof_lights") or []),
        "openings": len(candidate.get("openings") or []),
        "curtain_walls": len(candidate.get("curtain_walls") or []),
        "sweeps_and_cornices": len(candidate.get("sweeps") or []),
        "canopies": len(candidate.get("canopies") or []),
        "entrance_steps_and_ramps": len(candidate.get("access_elements") or []),
        "material_zone_boundaries": len(candidate.get("materials") or []),
        "interior_wall_terminations": len(candidate.get("internal_walls") or []),
    }


def create_confirmation_boundary(candidate: dict[str, Any]) -> dict[str, Any]:
    counts = boundary_candidate_counts(candidate)
    return {
        "presented_to_user": False,
        "must_review": {
            key: {
                "source_status": "PENDING",
                "white_model_status": "PENDING",
                "candidate_count": counts[key],
                "evidence_view_ids": [],
                "notes": "",
            }
            for key in BOUNDARY_KEYS
        },
        "allowed_to_ignore": list(ALLOWED_TO_IGNORE),
        "cad_detected_but_missing": [],
    }


def confirmation_boundary_errors(candidate: dict[str, Any], boundary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if boundary.get("presented_to_user") is not True:
        errors.append("topology confirmation boundary was not presented to the user")
    must_review = boundary.get("must_review") or {}
    if set(must_review) != set(BOUNDARY_KEYS):
        errors.append("confirmation boundary must cover every required topology review item exactly once")
    if tuple(boundary.get("allowed_to_ignore") or []) != ALLOWED_TO_IGNORE:
        errors.append("confirmation boundary allowed-to-ignore list is incomplete or changed")
    expected_counts = boundary_candidate_counts(candidate)
    detected_missing: list[str] = []
    for key in BOUNDARY_KEYS:
        item = must_review.get(key) or {}
        source_status = item.get("source_status")
        model_status = item.get("white_model_status")
        if source_status not in {"present", "not_applicable"}:
            errors.append(f"confirmation boundary {key} source status is unresolved")
        material_deferred = key == "material_zone_boundaries" and model_status == "user_deferred" and bool(item.get("scope_confirmation"))
        if model_status not in {"present", "not_applicable"} and not material_deferred:
            errors.append(f"confirmation boundary {key} white-model status is unresolved")
        if source_status == "present" and model_status != "present" and not material_deferred:
            detected_missing.append(key)
        if source_status == "not_applicable" and model_status != "not_applicable":
            errors.append(f"confirmation boundary {key} source/model applicability disagrees")
        if item.get("candidate_count") != expected_counts[key] and key not in {"entrance_steps_and_ramps", "material_zone_boundaries"}:
            errors.append(f"confirmation boundary {key} candidate count is stale")
        if not (item.get("evidence_view_ids") or []):
            errors.append(f"confirmation boundary {key} lacks source-view evidence")
        if not str(item.get("notes") or "").strip():
            errors.append(f"confirmation boundary {key} lacks a review note")
    declared_missing = sorted(str(item) for item in boundary.get("cad_detected_but_missing") or [])
    if declared_missing != sorted(detected_missing):
        errors.append("cad_detected_but_missing does not match the reviewed source/model inventory")
    if detected_missing:
        errors.append(f"CAD-detected topology is missing from the white model: {sorted(detected_missing)}")
    return errors


def core_validation_errors(project: Path, core: dict[str, Any]) -> list[str]:
    """Validate topology semantics before a white-model artifact exists."""
    provisional = {key: copy.deepcopy(core.get(key)) for key in CORE_CONTRACT_KEYS}
    provisional["access_elements"] = copy.deepcopy(core.get("access_elements") or [])
    provisional["columns"] = copy.deepcopy(core.get("columns") or [])
    source_reference = next((item for item in provisional.get("upstream") or [] if item.get("kind") == "source-index"), None)
    source_path = resolve(project, (source_reference or {}).get("path"))
    if source_path is None or not source_path.is_file():
        return ["topology core has no current source-index artifact"]
    source = load_json(source_path)
    source_hash = sha256_file(source_path)
    derivation_id = str((provisional.get("derivation") or {}).get("id") or "")
    review_views = [
        {
            "source_view_id": str(view.get("id")),
            "role": view.get("role"),
            "path": project_relative(project, source_path),
            "sha256": source_hash,
        }
        for view in source.get("views") or []
        if view.get("role") in {"plan", "roof_plan", "elevation", "section"} and view.get("include", True)
    ]
    provisional["schema"] = FINAL_SCHEMA
    provisional["status"] = "confirmed"
    provisional.pop("blocking_reasons", None)
    provisional.pop("white_model_output", None)
    provisional["white_model"] = {
        "model_path": project_relative(project, source_path),
        "model_sha256": source_hash,
        "stage": "topology_white_model",
        "review_views": review_views,
    }
    reviewer_derivation = f"core-check-{derivation_id or 'unknown'}"
    provisional["topology_audit"] = {
        "schema": REVIEW_SCHEMA,
        "status": "verified",
        "candidate_path": project_relative(project, source_path),
        "candidate_sha256": source_hash,
        "review_path": project_relative(project, source_path),
        "review_sha256": source_hash,
        "reviewer": {"id": "core-structure-check", "derivation_id": reviewer_derivation},
        "topology_derivation_id": derivation_id,
        "checks": {key: True for key in TOPOLOGY_AUDIT_CHECKS},
        "confirmation_boundary_presented": True,
    }
    provisional["user_confirmation"] = {
        "confirmed": True,
        "confirmation_id": "core-structure-check",
        "instruction": "core structure check only",
        "model_sha256": source_hash,
        "candidate_sha256": source_hash,
    }
    return [f"{issue.code}@{issue.location}: {issue.message}" for issue in validate_contract(project, "building-topology", provisional)]


def candidate_errors(project: Path, candidate: dict[str, Any], candidate_path: Path | None = None) -> list[str]:
    errors: list[str] = []
    if candidate.get("schema") != CANDIDATE_SCHEMA:
        errors.append(f"candidate schema must be {CANDIDATE_SCHEMA}")
    if candidate.get("status") != "candidate":
        errors.append("candidate status must be candidate")
    derivation = candidate.get("derivation") or {}
    if not derivation.get("id") or derivation.get("agent_role") != "topology_derivation":
        errors.append("candidate needs a topology_derivation ID")
    if not any(item.get("kind") == "source-index" for item in candidate.get("upstream") or []):
        errors.append("candidate must bind a source-index contract")
    if candidate.get("unresolved"):
        errors.append("candidate has unresolved model-driving topology")
    white_model = candidate.get("white_model") or {}
    model_path = resolve(project, white_model.get("model_path"))
    if model_path is None or not model_path.is_file():
        errors.append("topology white-model file is missing")
    elif sha256_file(model_path).lower() != str(white_model.get("model_sha256") or "").lower():
        errors.append("topology white-model hash is stale")
    registrations = candidate.get("registrations") or []
    if not registrations:
        errors.append("candidate has no registered model-driving views")
    registration_ids = [str(item.get("source_view_id") or "") for item in registrations]
    if "" in registration_ids or len(registration_ids) != len(set(registration_ids)):
        errors.append("candidate registrations need unique source_view_id values")
    review_views = {str(item.get("source_view_id") or ""): item for item in white_model.get("review_views") or []}
    missing_views = sorted(set(registration_ids) - set(review_views))
    if missing_views:
        errors.append(f"white model lacks review artifacts for {missing_views}")
    for view_id, view in review_views.items():
        evidence_path = resolve(project, view.get("path"))
        if evidence_path is None or not evidence_path.is_file():
            errors.append(f"white-model review artifact is missing for {view_id}")
        elif sha256_file(evidence_path).lower() != str(view.get("sha256") or "").lower():
            errors.append(f"white-model review artifact hash is stale for {view_id}")
    if candidate_path is not None and candidate_path.is_file() and model_path is not None and model_path.is_file():
        provisional = copy.deepcopy(candidate)
        provisional["schema"] = FINAL_SCHEMA
        provisional["status"] = "confirmed"
        candidate_sha = sha256_file(candidate_path)
        provisional["topology_audit"] = {
            "schema": REVIEW_SCHEMA,
            "status": "verified",
            "candidate_path": project_relative(project, candidate_path),
            "candidate_sha256": candidate_sha,
            "review_path": project_relative(project, candidate_path),
            "review_sha256": candidate_sha,
            "reviewer": {"id": "candidate-structure-check", "derivation_id": "candidate-structure-check"},
            "topology_derivation_id": derivation.get("id"),
            "checks": {key: True for key in TOPOLOGY_AUDIT_CHECKS},
            "confirmation_boundary_presented": True,
        }
        provisional["user_confirmation"] = {
            "confirmed": True,
            "confirmation_id": "candidate-structure-check",
            "instruction": "candidate structure check only",
            "model_sha256": white_model.get("model_sha256"),
            "candidate_sha256": candidate_sha,
        }
        ignored_codes = {"topology.audit_independence"}
        for issue in validate_contract(project, "building-topology", provisional):
            if issue.code not in ignored_codes:
                errors.append(f"{issue.code}@{issue.location}: {issue.message}")
    return errors


def create_review_template(project: Path, candidate_path: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    errors = candidate_errors(project, candidate, candidate_path)
    if errors:
        raise ValueError("; ".join(errors))
    review_views = {
        str(item.get("source_view_id")): item
        for item in (candidate.get("white_model") or {}).get("review_views") or []
    }
    view_results = []
    for registration in candidate.get("registrations") or []:
        source_view_id = str(registration.get("source_view_id"))
        artifact = review_views[source_view_id]
        view_results.append(
            {
                "source_view_id": source_view_id,
                "qa_view": registration.get("qa_view"),
                "status": "PENDING",
                "evidence_path": artifact.get("path"),
                "evidence_sha256": artifact.get("sha256"),
                "checks": {key: None for key in VIEW_CHECKS},
                "notes": "",
            }
        )
    candidate_sha = sha256_file(candidate_path)
    return {
        "schema": REVIEW_SCHEMA,
        "candidate_sha256": candidate_sha,
        "topology_derivation_id": (candidate.get("derivation") or {}).get("id"),
        "reviewer": {"id": "", "derivation_id": ""},
        "status": "pending",
        "view_results": view_results,
        "checks": {key: None for key in TOPOLOGY_AUDIT_CHECKS},
        "confirmation_boundary": create_confirmation_boundary(candidate),
        "unresolved": [],
        "user_confirmation": {
            "confirmed": False,
            "confirmation_id": "",
            "instruction": "",
            "model_sha256": (candidate.get("white_model") or {}).get("model_sha256"),
            "candidate_sha256": candidate_sha,
        },
    }


def review_errors(project: Path, candidate_path: Path, candidate: dict[str, Any], review: dict[str, Any]) -> list[str]:
    errors = candidate_errors(project, candidate, candidate_path)
    candidate_sha = sha256_file(candidate_path)
    if review.get("schema") != REVIEW_SCHEMA:
        errors.append(f"review schema must be {REVIEW_SCHEMA}")
    if str(review.get("candidate_sha256") or "").lower() != candidate_sha.lower():
        errors.append("review is not bound to the current topology candidate")
    topology_derivation_id = str((candidate.get("derivation") or {}).get("id") or "")
    if review.get("topology_derivation_id") != topology_derivation_id:
        errors.append("review topology_derivation_id does not match candidate")
    reviewer = review.get("reviewer") or {}
    if not reviewer.get("id") or not reviewer.get("derivation_id"):
        errors.append("independent reviewer ID and derivation ID are required")
    if reviewer.get("derivation_id") == topology_derivation_id:
        errors.append("review derivation must differ from topology derivation")
    if review.get("status") != "verified":
        errors.append("review status must be verified")
    expected_views = {
        str(item.get("source_view_id")): item
        for item in candidate.get("registrations") or []
    }
    results = review.get("view_results") or []
    result_ids = [str(item.get("source_view_id") or "") for item in results]
    if set(result_ids) != set(expected_views) or len(result_ids) != len(set(result_ids)):
        errors.append("review view_results must cover every registered source view exactly once")
    for index, result in enumerate(results):
        location = f"view_results[{index}]"
        expected = expected_views.get(str(result.get("source_view_id"))) or {}
        if result.get("qa_view") != expected.get("qa_view"):
            errors.append(f"{location} QA key does not match candidate registration")
        if result.get("status") != "PASS":
            errors.append(f"{location} did not pass")
        for key in VIEW_CHECKS:
            if (result.get("checks") or {}).get(key) is not True:
                errors.append(f"{location}.{key} did not pass")
        evidence_path = resolve(project, result.get("evidence_path"))
        if evidence_path is None or not evidence_path.is_file():
            errors.append(f"{location} evidence file is missing")
        elif sha256_file(evidence_path).lower() != str(result.get("evidence_sha256") or "").lower():
            errors.append(f"{location} evidence hash is stale")
    for key in TOPOLOGY_AUDIT_CHECKS:
        if (review.get("checks") or {}).get(key) is not True:
            errors.append(f"independent audit check {key} did not pass")
    errors.extend(confirmation_boundary_errors(candidate, review.get("confirmation_boundary") or {}))
    if review.get("unresolved"):
        errors.append("independent topology review has unresolved findings")
    confirmation = review.get("user_confirmation") or {}
    if confirmation.get("confirmed") is not True or not confirmation.get("confirmation_id") or not confirmation.get("instruction"):
        errors.append("explicit user confirmation of the reviewed topology white model is required")
    if str(confirmation.get("model_sha256") or "").lower() != str((candidate.get("white_model") or {}).get("model_sha256") or "").lower():
        errors.append("user confirmation is not bound to the current white model")
    if str(confirmation.get("candidate_sha256") or "").lower() != candidate_sha.lower():
        errors.append("user confirmation is not bound to the current topology candidate")
    return errors


def scope_deferral_errors(project: Path, review: dict[str, Any]) -> list[str]:
    errors=[]
    for key, item in (review.get("confirmation_boundary", {}).get("must_review") or {}).items():
        if item.get("white_model_status") != "user_deferred":
            continue
        proof=item.get("scope_confirmation") or {}
        path=resolve(project,proof.get("path"))
        if key != "material_zone_boundaries" or path is None or not path.is_file() or sha256_file(path)!=proof.get("sha256"):
            errors.append("Deferred white-model materials require current explicit user scope evidence; geometry cannot be deferred here")
    return errors


def promote(project: Path, candidate_path: Path, review_path: Path) -> dict[str, Any]:
    candidate = load_json(candidate_path)
    review = load_json(review_path)
    scope_errors=scope_deferral_errors(project,review)
    if scope_errors:
        raise ValueError("\n".join(scope_errors))
    errors = review_errors(project, candidate_path, candidate, review)
    if errors:
        raise ValueError("; ".join(errors))
    promoted = copy.deepcopy(candidate)
    promoted["schema"] = FINAL_SCHEMA
    promoted["status"] = "confirmed"
    promoted["topology_audit"] = {
        "schema": REVIEW_SCHEMA,
        "status": "verified",
        "candidate_path": project_relative(project, candidate_path),
        "candidate_sha256": sha256_file(candidate_path),
        "review_path": project_relative(project, review_path),
        "review_sha256": sha256_file(review_path),
        "reviewer": review["reviewer"],
        "topology_derivation_id": review["topology_derivation_id"],
        "checks": review["checks"],
        "confirmation_boundary_presented": True,
    }
    promoted["user_confirmation"] = review["user_confirmation"]
    issues = validate_contract(project, "building-topology", promoted)
    if issues:
        raise ValueError("; ".join(f"{item.code}@{item.location}: {item.message}" for item in issues))
    return promoted


def main() -> int:
    parser = argparse.ArgumentParser(description="Independently audit and promote a building-topology candidate.")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--review-template", type=Path)
    mode.add_argument("--review", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    project = args.project.resolve()
    candidate_path = args.candidate if args.candidate.is_absolute() else project / args.candidate
    if args.review_template:
        output = args.review_template if args.review_template.is_absolute() else project / args.review_template
        write_json(output, create_review_template(project, candidate_path, load_json(candidate_path)))
        print(json.dumps({"status": "review_template_created", "path": str(output)}, ensure_ascii=False))
        return 0
    if not args.out:
        parser.error("--out is required with --review")
    review_path = args.review if args.review.is_absolute() else project / args.review
    output = args.out if args.out.is_absolute() else project / args.out
    from review_session import require_receipt
    require_receipt(project, review_path, "topology")
    write_json(output, promote(project, candidate_path, review_path))
    print(json.dumps({"status": "confirmed", "path": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
