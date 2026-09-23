#!/usr/bin/env python3
"""Validate CAD-to-SketchUp workflow gates from project evidence.

This script does not decide whether drawings or a model are correct. It checks
that the required evidence, explicit confirmations, and independent QA records
exist before the workflow advances.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contract_validation import load_json as load_contract_json
from contract_validation import validate_contract, validate_schema_file
from audit_building_topology import core_validation_errors
from create_sketchup_build_derivation import topology_ids
from delivery_validation import validate_delivery_manifest

SCHEMA = "cad_to_sketchup.workflow.2026-08-04"
LEGACY_SCHEMA = "cad_to_sketchup.workflow.2026-07-24"
DEFAULT_DELIVERY_VIEWS = {
    "plan",
    "south_elevation",
    "north_elevation",
    "east_elevation",
    "west_elevation",
}
MODEL_DRIVING_ROLES = {"plan", "roof_plan", "elevation", "section"}
READING_CHECKS = {
    "full_frame",
    "title_visible",
    "readable_annotations",
    "axis_bubbles_visible",
    "not_clipped",
    "separated_drawing_type",
    "source_coverage_checked",
    "view_inventory_confirmed",
}
CAD_PROVENANCE_KINDS = {"dxf_extract", "cad_export", "confirmed_drawing_measurement"}
MODEL_PROVENANCE_KINDS = {"active_skp_export", "active_skp_measurement"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def project_path(project: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return path if path.is_absolute() else project / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class GateReport:
    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.blockers: list[dict[str, str]] = []

    def require(self, condition: bool, code: str, location: str, message: str) -> None:
        if not condition:
            self.blockers.append({"code": code, "location": location, "message": message})

    def require_file(self, project: Path, value: Any, code: str, location: str) -> Path | None:
        path = project_path(project, value)
        self.require(path is not None, code, location, "Evidence path is missing.")
        if path is not None:
            self.require(path.is_file(), code, location, f"Evidence file does not exist: {path}")
        return path

    def require_hash(self, path: Path | None, expected: Any, code: str, location: str) -> None:
        self.require(isinstance(expected, str) and len(expected) == 64, code, location,
                     "A full SHA-256 fingerprint is required.")
        if path is not None and path.is_file() and isinstance(expected, str) and len(expected) == 64:
            self.require(sha256_file(path).lower() == expected.lower(), code, location,
                         "Evidence fingerprint is stale or does not match the current file.")

    @property
    def passed(self) -> bool:
        return not self.blockers


def validate_contract_entry(
    project: Path,
    state: dict[str, Any],
    kind: str,
    report: GateReport,
) -> dict[str, Any] | None:
    entry = (state.get("contracts") or {}).get(kind) or {}
    location = f"contracts.{kind}"
    path = report.require_file(project, entry.get("path"), "contract.missing", f"{location}.path")
    report.require_hash(path, entry.get("sha256"), "contract.invalid_hash", f"{location}.sha256")
    if path is None or not path.is_file():
        return None
    try:
        data = load_contract_json(path)
    except (OSError, ValueError, TypeError) as exc:
        report.require(False, "contract.invalid_json", f"{location}.path", str(exc))
        return None
    for issue in validate_contract(project, kind, data):
        report.require(False, issue.code, f"{location}:{issue.location}", issue.message)
    return data


def validate_current_reading(project: Path, state: dict[str, Any], report: GateReport) -> None:
    validate_contract_entry(project, state, "source-index", report)


def validate_current_topology(project: Path, state: dict[str, Any], report: GateReport) -> None:
    """Authorize only the pre-confirmation topology white-model build."""
    validate_current_reading(project, state, report)
    entry = state.get("topology_plan") or {}
    location = "topology_plan"
    path = report.require_file(project, entry.get("path"), "topology_plan.missing", f"{location}.path")
    report.require_hash(path, entry.get("sha256"), "topology_plan.invalid_hash", f"{location}.sha256")
    if path is not None and path.is_file():
        try:
            plan = load_contract_json(path)
        except (OSError, ValueError, TypeError) as exc:
            report.require(False, "topology_plan.invalid_json", f"{location}.path", str(exc))
        else:
            for issue in validate_schema_file("topology-white-model-plan.schema.json", plan):
                report.require(False, issue.code, f"{location}:{issue.location}", issue.message)
            for message in core_validation_errors(project, plan):
                report.require(False, "topology_plan.semantic", location, message)
            report.require(plan.get("status") == "ready_for_white_model", "topology_plan.not_ready", f"{location}.status", "Topology plan is not ready for white-model generation.")
            report.require(plan.get("execution_allowed") is True, "topology_plan.execution_denied", f"{location}.execution_allowed", "Topology plan explicitly denies execution.")
            try:
                plan_project = Path(str(plan.get("project_root") or "")).resolve()
            except (OSError, ValueError, TypeError):
                plan_project = None
            report.require(plan_project == project.resolve(), "topology_plan.wrong_project", f"{location}.project_root", "Topology plan belongs to a different project root.")
    target = state.get("sketchup_target") or {}
    report.require(target.get("confirmed") is True, "target.unconfirmed", "sketchup_target.confirmed", "Confirm the exact user-opened SketchUp target before generating the topology white model.")


def validate_current_build(project: Path, state: dict[str, Any], report: GateReport) -> None:
    validate_current_reading(project, state, report)
    topology = validate_contract_entry(project, state, "building-topology", report)
    entry = state.get("production_plan") or {}
    location = "production_plan"
    plan = None
    path = report.require_file(project, entry.get("path"), "production_plan.missing", f"{location}.path")
    report.require_hash(path, entry.get("sha256"), "production_plan.invalid_hash", f"{location}.sha256")
    if path is not None and path.is_file():
        try:
            plan = load_contract_json(path)
        except (OSError, ValueError, TypeError) as exc:
            report.require(False, "production_plan.invalid_json", f"{location}.path", str(exc))
        else:
            for issue in validate_schema_file("sketchup-production-plan.schema.json", plan):
                report.require(False, issue.code, f"{location}:{issue.location}", issue.message)
            report.require(plan.get("status") == "ready_for_sketchup" and plan.get("execution_allowed") is True, "production_plan.not_ready", location, "Production plan is not execution-authorized.")
            try:
                plan_project = Path(str(plan.get("project_root") or "")).resolve()
            except (OSError, ValueError, TypeError):
                plan_project = None
            report.require(plan_project == project.resolve(), "production_plan.wrong_project", f"{location}.project_root", "Production plan belongs to a different project root.")
            if topology:
                expected_ids = set(topology_ids(topology))
                actual_ids = set(plan.get("required_topology_ids") or [])
                report.require(actual_ids == expected_ids, "production_plan.coverage", f"{location}.required_topology_ids", f"Production plan topology coverage differs; missing={sorted(expected_ids - actual_ids)} extra={sorted(actual_ids - expected_ids)}")
                topology_state = ((state.get("contracts") or {}).get("building-topology") or {})
                topology_ref = next((item for item in plan.get("upstream") or [] if item.get("kind") == "building-topology"), {})
                report.require(str(topology_ref.get("sha256") or "").lower() == str(topology_state.get("sha256") or "").lower(), "production_plan.topology_stale", f"{location}.upstream", "Production plan is not bound to the current confirmed topology.")
                target = plan.get("target") or {}
                report.require(str(target.get("source_model_sha256") or "").lower() == str((topology.get("white_model") or {}).get("model_sha256") or "").lower(), "production_plan.source_model_stale", f"{location}.target.source_model_sha256", "Production plan does not start from the confirmed white model.")
    state_target = state.get("sketchup_target") or {}
    report.require(state_target.get("confirmed") is True, "target.unconfirmed", "sketchup_target.confirmed", "Confirm the exact user-opened SketchUp target.")
    if plan is not None:
        selected = project_path(project, state_target.get("model_path"))
        plan_target = plan.get("target") or {}
        allowed = {project_path(project, plan_target.get("source_model_path")), project_path(project, plan_target.get("output_model_path"))}
        allowed = {item.resolve() for item in allowed if item is not None}
        report.require(selected is not None and selected.resolve() in allowed, "target.wrong_model", "sketchup_target.model_path", "Confirmed SketchUp target must be the plan's source white model before build or production output after build.")


def validate_current_qa(project: Path, state: dict[str, Any], report: GateReport) -> None:
    validate_current_build(project, state, report)
    validate_contract_entry(project, state, "sketchup-build", report)


def validate_current_delivery(project: Path, state: dict[str, Any], report: GateReport) -> None:
    validate_current_qa(project, state, report)
    qa_contract = validate_contract_entry(project, state, "independent-qa", report)
    confirmation = (qa_contract or {}).get("user_confirmation") or {}
    report.require(confirmation.get("confirmed") is True, "delivery.user_confirmation", "contracts.independent-qa:user_confirmation.confirmed", "Final user acceptance is required after independent QA passes.")
    entry = state.get("delivery_manifest") or {}
    path = report.require_file(project, entry.get("path"), "delivery.manifest_missing", "delivery_manifest.path")
    report.require_hash(path, entry.get("sha256"), "delivery.manifest_hash", "delivery_manifest.sha256")
    if path is not None and path.is_file():
        try:
            manifest = load_contract_json(path)
        except (OSError, ValueError, TypeError) as exc:
            report.require(False, "delivery.manifest_json", "delivery_manifest.path", str(exc))
        else:
            for message in validate_delivery_manifest(project, manifest):
                report.require(False, "delivery.manifest_invalid", "delivery_manifest", message)
            qa_state = ((state.get("contracts") or {}).get("independent-qa") or {})
            qa_ref = next((item for item in manifest.get("source_contracts") or [] if item.get("kind") == "independent-qa"), {})
            report.require(qa_ref.get("path") == qa_state.get("path") and str(qa_ref.get("sha256") or "").lower() == str(qa_state.get("sha256") or "").lower(), "delivery.manifest_qa", "delivery_manifest.source_contracts", "Delivery package is not bound to the current accepted QA contract.")


def validate_input(project: Path, state: dict[str, Any], report: GateReport) -> None:
    scope = state.get("input_scope") or {}
    report.require(scope.get("confirmed") is True, "input.unconfirmed", "input_scope.confirmed",
                   "The user has not confirmed the input scope.")
    sources = scope.get("sources") or []
    report.require(bool(sources), "input.empty", "input_scope.sources",
                   "At least one approved CAD source is required.")
    for index, source in enumerate(sources):
        location = f"input_scope.sources[{index}]"
        source_path = report.require_file(
            project, source.get("path"), "input.missing_file", f"{location}.path"
        )
        report.require(bool(source.get("role")), "input.missing_role", f"{location}.role",
                       "Every source needs an explicit role.")
        report.require_hash(source_path, source.get("sha256"), "input.invalid_hash",
                            f"{location}.sha256")

    units = state.get("units") or {}
    report.require(units.get("value") in {"mm", "cm", "m", "in", "ft"},
                   "units.invalid", "units.value", "Record a supported unit.")
    report.require(units.get("basis") in {"cad_header", "user_confirmed"},
                   "units.unconfirmed", "units.basis",
                   "Units must come from the CAD header or explicit user confirmation.")


def validate_reading(project: Path, state: dict[str, Any], report: GateReport) -> None:
    validate_input(project, state, report)
    scope = state.get("drawing_scope") or {}
    report.require(scope.get("confirmed") is True, "drawing_scope.unconfirmed",
                   "drawing_scope.confirmed", "The complete drawing package is not confirmed.")
    regions_path = report.require_file(
        project, scope.get("regions_file"), "drawing_scope.missing_regions",
        "drawing_scope.regions_file"
    )
    report.require_hash(regions_path, scope.get("regions_sha256"),
                        "drawing_scope.invalid_hash", "drawing_scope.regions_sha256")
    if regions_path is None or not regions_path.is_file():
        return

    try:
        package = load_json(regions_path)
    except (OSError, ValueError, TypeError) as exc:
        report.require(False, "drawing_scope.invalid_json", "drawing_scope.regions_file",
                       f"Cannot read region package: {exc}")
        return

    regions = [
        region for region in package.get("regions", [])
        if region.get("role") in MODEL_DRIVING_ROLES and region.get("include", True)
    ]
    frames = package.get("frames") or []
    report.require(bool(frames), "drawing_scope.no_frames", "frames",
                   "The CAD reader package has no complete drawing frames.")
    frame_ids = set()
    for frame in frames:
        frame_id = str(frame.get("id") or "UNKNOWN")
        location = f"frames.{frame_id}"
        frame_ids.add(frame_id)
        report.require(frame.get("confirmation_state") == "confirmed",
                       "drawing_scope.frame_unconfirmed", f"{location}.confirmation_state",
                       "Every drawing frame must be explicitly confirmed.")
        report.require_file(project, frame.get("preview_jpg"),
                            "drawing_scope.missing_frame_preview", f"{location}.preview_jpg")
        report.require_file(project, frame.get("vector_master"),
                            "drawing_scope.missing_vector_master", f"{location}.vector_master")
        coverage = frame.get("render_coverage") or {}
        report.require(coverage.get("status") == "confirmed",
                       "drawing_scope.render_coverage", f"{location}.render_coverage.status",
                       "Source-to-render coverage has not been independently confirmed.")
        if frame.get("mixed_view_types") is True:
            model_views = [
                view for view in frame.get("views", [])
                if any(role in MODEL_DRIVING_ROLES for role in view.get("detected_roles", []))
            ]
            report.require(len(model_views) > 1, "drawing_scope.collapsed_mixed_frame",
                           f"{location}.views",
                           "A mixed frame must retain its separate model-driving views.")
    report.require(bool(regions), "drawing_scope.no_model_regions", "regions",
                   "No model-driving plan, elevation, roof plan, or section is included.")
    report.require(any(region.get("role") == "plan" for region in regions),
                   "drawing_scope.no_plan", "regions", "At least one confirmed plan is required.")

    for region in regions:
        region_id = str(region.get("id") or "UNKNOWN")
        location = f"regions.{region_id}"
        report.require(bool(region.get("frame_id")) and region.get("frame_id") in frame_ids,
                       "drawing_scope.invalid_frame_link", f"{location}.frame_id",
                       "Every model-driving view must link to an owning confirmed frame.")
        report.require(region.get("confirmation_state") == "confirmed",
                       "drawing_scope.region_unconfirmed", f"{location}.confirmation_state",
                       "Every model-driving region must be explicitly confirmed.")
        report.require_file(project, region.get("preview_jpg"),
                            "drawing_scope.missing_preview", f"{location}.preview_jpg")
        checks = region.get("reading_checks") or {}
        for check in READING_CHECKS:
            report.require(checks.get(check) is True, f"drawing_scope.{check}",
                           f"{location}.reading_checks.{check}",
                           f"Reading check '{check}' has not passed.")


def validate_interpretation(project: Path, state: dict[str, Any], report: GateReport) -> None:
    interpretation = state.get("interpretation") or {}
    report.require(interpretation.get("confirmed") is True, "interpretation.unconfirmed",
                   "interpretation.confirmed", "Model intent has not been explicitly confirmed.")
    for key in ("source_geometry_file", "model_intent_file", "build_plan_file"):
        path = report.require_file(project, interpretation.get(key), "interpretation.missing_artifact",
                                   f"interpretation.{key}")
        hash_key = key.replace("_file", "_sha256")
        report.require_hash(path, interpretation.get(hash_key), "interpretation.invalid_hash",
                            f"interpretation.{hash_key}")

    build_plan_path = project_path(project, interpretation.get("build_plan_file"))
    if build_plan_path and build_plan_path.is_file():
        try:
            build_plan = load_json(build_plan_path)
        except (OSError, ValueError, TypeError) as exc:
            report.require(False, "interpretation.invalid_build_plan",
                           "interpretation.build_plan_file", f"Cannot read build plan: {exc}")
        else:
            report.require(build_plan.get("execution_allowed") is True,
                           "interpretation.review_only_plan", "build_plan.execution_allowed",
                           "The build plan is still review-only.")
            report.require(bool(build_plan.get("operations")),
                           "interpretation.empty_build_plan", "build_plan.operations",
                           "The confirmed build plan has no operations.")


def validate_build(project: Path, state: dict[str, Any], report: GateReport) -> None:
    validate_reading(project, state, report)
    validate_interpretation(project, state, report)
    approval = state.get("modeling_approval") or {}
    report.require(approval.get("confirmed") is True, "approval.missing",
                   "modeling_approval.confirmed", "The user has not explicitly approved modeling.")
    report.require(bool(approval.get("instruction")), "approval.missing_instruction",
                   "modeling_approval.instruction", "Record the user's modeling instruction.")
    target = state.get("sketchup_target") or {}
    report.require(target.get("confirmed") is True, "target.unconfirmed",
                   "sketchup_target.confirmed", "The exact SketchUp target is not confirmed.")


def validate_provenance(
    project: Path,
    provenance: dict[str, Any],
    allowed_kinds: set[str],
    prefix: str,
    report: GateReport,
) -> Path | None:
    report.require(provenance.get("kind") in allowed_kinds, f"qa.{prefix}_kind",
                   f"qa.{prefix}_provenance.kind", "Evidence provenance kind is invalid.")
    return report.require_file(project, provenance.get("path"), f"qa.{prefix}_source",
                               f"qa.{prefix}_provenance.path")


def validate_delivery(project: Path, state: dict[str, Any], report: GateReport) -> None:
    validate_build(project, state, report)
    delivery_scope = state.get("delivery_scope") or {}
    report.require(delivery_scope.get("level") == "full_deliverable",
                   "delivery.candidate_scope", "delivery_scope.level",
                   "Candidate scope cannot be labeled Delivered.")
    qa = state.get("qa") or {}
    active_model = report.require_file(
        project, qa.get("active_model"), "qa.missing_model", "qa.active_model"
    )
    report.require_hash(active_model, qa.get("active_model_sha256"),
                        "qa.invalid_model_hash", "qa.active_model_sha256")
    report.require(qa.get("status") == "PASS", "qa.not_passed", "qa.status",
                   "The complete QA package has not passed.")

    cad_provenance = qa.get("cad_provenance") or {}
    model_provenance = qa.get("model_provenance") or {}
    cad_path = validate_provenance(project, cad_provenance, CAD_PROVENANCE_KINDS, "cad", report)
    model_path = validate_provenance(project, model_provenance, MODEL_PROVENANCE_KINDS, "model", report)
    report.require(bool(cad_provenance.get("derivation_id")), "qa.cad_derivation",
                   "qa.cad_provenance.derivation_id", "CAD derivation ID is required.")
    report.require(bool(model_provenance.get("derivation_id")), "qa.model_derivation",
                   "qa.model_provenance.derivation_id", "Model derivation ID is required.")
    if cad_path and model_path:
        report.require(cad_path.resolve() != model_path.resolve(), "qa.self_referential",
                       "qa.provenance", "CAD and model measurements cannot use the same source file.")
    report.require(cad_provenance.get("derivation_id") != model_provenance.get("derivation_id"),
                   "qa.same_derivation", "qa.provenance",
                   "CAD and model measurements cannot share one derivation.")

    alignment_report = report.require_file(
        project, qa.get("alignment_report"), "qa.missing_alignment_report",
        "qa.alignment_report"
    )
    report.require_hash(alignment_report, qa.get("alignment_report_sha256"),
                        "qa.invalid_alignment_hash", "qa.alignment_report_sha256")
    required_views = set(delivery_scope.get("required_views") or DEFAULT_DELIVERY_VIEWS)
    regions_path = project_path(project, (state.get("drawing_scope") or {}).get("regions_file"))
    if regions_path and regions_path.is_file():
        package = load_json(regions_path)
        for region in package.get("regions", []):
            if region.get("role") not in MODEL_DRIVING_ROLES or not region.get("include", True):
                continue
            region_id = str(region.get("id") or "UNKNOWN")
            qa_view = region.get("qa_view")
            report.require(bool(qa_view), "delivery.missing_region_view",
                           f"regions.{region_id}.qa_view",
                           "Every model-driving region needs a delivery QA view key.")
            if qa_view:
                required_views.add(str(qa_view))
    report.require(DEFAULT_DELIVERY_VIEWS.issubset(required_views),
                   "delivery.incomplete_view_scope", "delivery_scope.required_views",
                   "Full delivery requires plan plus south, north, east, and west elevations.")
    passed_views = set(qa.get("passed_views") or [])
    missing_views = sorted(required_views - passed_views)
    report.require(not missing_views, "qa.missing_views", "qa.passed_views",
                   f"Required views have not passed: {', '.join(missing_views)}")

    overlays = qa.get("visual_overlays") or {}
    overlay_results = qa.get("overlay_results") or {}
    for view in sorted(required_views):
        report.require_file(project, overlays.get(view), "qa.missing_overlay",
                            f"qa.visual_overlays.{view}")
        result = overlay_results.get(view) or {}
        report.require(result.get("status") == "PASS", "qa.overlay_not_passed",
                       f"qa.overlay_results.{view}.status",
                       f"Independent same-view overlay has not passed: {view}")

    report.require(qa.get("silhouette_pass") is True, "qa.silhouette",
                   "qa.silhouette_pass", "Complete elevation silhouettes have not passed.")
    report.require(qa.get("opening_outline_pass") is True, "qa.opening_outline",
                   "qa.opening_outline_pass", "CAD opening-outline completeness has not passed.")
    report.require(qa.get("real_openings_pass") is True, "qa.real_openings",
                   "qa.real_openings_pass", "True wall openings and components have not passed.")
    report.require(qa.get("debug_geometry_clear") is True, "qa.debug_geometry",
                   "qa.debug_geometry_clear", "Visible QA/cutter/debug geometry remains.")
    report.require(qa.get("semantic_geometry_pass") is True, "qa.semantic_geometry",
                   "qa.semantic_geometry_pass",
                   "CAD lines or images cannot substitute for the required 3D architectural elements.")
    report.require(qa.get("cross_view_consistency_pass") is True, "qa.cross_view_consistency",
                   "qa.cross_view_consistency_pass",
                   "Plan, elevation, and section constraints have not been reconciled.")
    report.require(not (qa.get("unresolved") or []), "qa.unresolved", "qa.unresolved",
                   "Unresolved model-driving discrepancies block delivery.")


def validate(project: Path, state: dict[str, Any], stage: str) -> GateReport:
    report = GateReport(stage)
    schema = state.get("schema")
    report.require(schema in {SCHEMA, LEGACY_SCHEMA}, "state.schema", "schema",
                   f"Expected workflow schema '{SCHEMA}' or legacy '{LEGACY_SCHEMA}'.")
    if schema == SCHEMA:
        if stage == "reading":
            validate_current_reading(project, state, report)
        elif stage == "topology":
            validate_current_topology(project, state, report)
        elif stage == "build":
            validate_current_build(project, state, report)
        elif stage == "qa":
            validate_current_qa(project, state, report)
        elif stage == "delivery":
            validate_current_delivery(project, state, report)
        else:
            raise ValueError(f"Unsupported stage: {stage}")
    elif schema == LEGACY_SCHEMA:
        if stage == "reading":
            validate_reading(project, state, report)
        elif stage == "build":
            validate_build(project, state, report)
        elif stage == "delivery":
            validate_delivery(project, state, report)
        else:
            raise ValueError(f"Unsupported legacy stage: {stage}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CAD-to-SketchUp workflow gates.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--stage", required=True, choices=("reading", "topology", "build", "qa", "delivery"))
    parser.add_argument("--state", default="work/workflow-state.json")
    parser.add_argument("--output")
    args = parser.parse_args()

    project = args.project.resolve()
    state_path = project_path(project, args.state)
    if state_path is None or not state_path.is_file():
        result = {
            "schema": "cad_to_sketchup.gate_report.2026-08-04",
            "stage": args.stage,
            "status": "BLOCKED",
            "blockers": [{
                "code": "state.missing",
                "location": str(state_path or args.state),
                "message": "Create work/workflow-state.json from the workflow contract.",
            }],
        }
    else:
        try:
            state = load_json(state_path)
            report = validate(project, state, args.stage)
            result = {
                "schema": "cad_to_sketchup.gate_report.2026-08-04",
                "stage": args.stage,
                "status": "PASS" if report.passed else "BLOCKED",
                "blockers": report.blockers,
            }
        except (OSError, ValueError, TypeError) as exc:
            result = {
                "schema": "cad_to_sketchup.gate_report.2026-08-04",
                "stage": args.stage,
                "status": "BLOCKED",
                "blockers": [{
                    "code": "state.invalid",
                    "location": str(state_path),
                    "message": str(exc),
                }],
            }

    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = project_path(project, args.output)
        if output is None:
            raise ValueError("Output path is empty.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
