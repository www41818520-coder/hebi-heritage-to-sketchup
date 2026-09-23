#!/usr/bin/env python3
"""Semantic validation for the four CAD-to-SketchUp stage contracts."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, RefResolver


SCHEMAS = {
    "source-index": "cad_to_sketchup.source_index.2026-08-05",
    "building-topology": "cad_to_sketchup.building_topology.2026-08-05",
    "sketchup-build": "cad_to_sketchup.sketchup_build.2026-08-06",
    "independent-qa": "cad_to_sketchup.independent_qa.2026-08-06",
}
SCHEMA_FILES = {
    "source-index": "source-index.schema.json",
    "building-topology": "building-topology.schema.json",
    "sketchup-build": "sketchup-build.schema.json",
    "independent-qa": "independent-qa.schema.json",
}
UPSTREAM_KINDS = set(SCHEMAS) | {"independent-qa-derivation"}
MODEL_ROLES = {"plan", "roof_plan", "elevation", "section"}
GEOMETRY_EVIDENCE = {
    "measured_plan",
    "measured_elevation",
    "measured_section",
    "measured_end_profile",
    "explicit_user_control",
}
TOPOLOGY_AUDIT_CHECKS = (
    "all_required_views_registered",
    "floor_footprints_match",
    "wall_rings_closed",
    "slabs_contained",
    "openings_registered_and_hosted",
    "curtain_walls_registered",
    "sweep_paths_profiles_and_corners_complete",
    "canopies_cross_view_complete",
    "roofs_registered",
    "no_unsupported_geometry",
    "white_model_readable",
)


@dataclass(frozen=True)
class Issue:
    code: str
    location: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "location": self.location, "message": self.message}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def qa_acceptance_basis_sha256(data: dict[str, Any]) -> str:
    """Fingerprint the verified QA evidence before final user acceptance."""
    basis = copy.deepcopy(data)
    basis["user_confirmation"] = {"confirmed": False}
    encoded = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_path(project: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return path if path.is_absolute() else project / path


class Validator:
    def __init__(self, project: Path, kind: str, data: dict[str, Any]) -> None:
        self.project = project.resolve()
        self.kind = kind
        self.data = data
        self.issues: list[Issue] = []

    def require(self, condition: bool, code: str, location: str, message: str) -> None:
        if not condition:
            self.issues.append(Issue(code, location, message))

    def validate(self) -> list[Issue]:
        self.require(self.kind in SCHEMAS, "contract.kind", "kind", "Unknown contract kind.")
        if self.kind not in SCHEMAS:
            return self.issues
        self.require(
            self.data.get("schema") == SCHEMAS[self.kind],
            "contract.schema",
            "schema",
            f"Expected schema {SCHEMAS[self.kind]}.",
        )
        self.require(bool(self.data.get("contract_id")), "contract.id", "contract_id", "Contract ID is required.")
        self.require(bool(self.data.get("project_id")), "contract.project", "project_id", "Project ID is required.")
        self.require(
            isinstance(self.data.get("revision"), int) and self.data.get("revision", 0) >= 1,
            "contract.revision",
            "revision",
            "Revision must be a positive integer.",
        )
        self._validate_upstream()
        getattr(self, f"_validate_{self.kind.replace('-', '_')}")()
        return self.issues

    def _validate_upstream(self) -> None:
        upstream = self.data.get("upstream") or []
        if self.kind != "source-index":
            self.require(bool(upstream), "upstream.empty", "upstream", "Downstream contracts require hash-bound upstream evidence.")
        seen: set[str] = set()
        for index, item in enumerate(upstream):
            location = f"upstream[{index}]"
            kind = item.get("kind")
            self.require(kind in UPSTREAM_KINDS, "upstream.kind", f"{location}.kind", "Upstream kind is invalid.")
            self.require(kind not in seen, "upstream.duplicate", f"{location}.kind", "Upstream kind is duplicated.")
            seen.add(str(kind))
            path = resolve_path(self.project, item.get("path"))
            self.require(path is not None and path.is_file(), "upstream.missing", f"{location}.path", "Upstream contract file is missing.")
            expected = item.get("sha256")
            self.require(isinstance(expected, str) and len(expected) == 64, "upstream.hash", f"{location}.sha256", "A full SHA-256 is required.")
            if path is not None and path.is_file() and isinstance(expected, str) and len(expected) == 64:
                self.require(sha256_file(path).lower() == expected.lower(), "upstream.stale", location, "Upstream contract changed; invalidate this contract.")

    def _validate_source_index(self) -> None:
        self.require(self.data.get("status") == "verified", "source.status", "status", "Source index must be verified.")
        reader_audit = self.data.get("reader_audit") or {}
        self.require(reader_audit.get("schema") == "cad_reader.independent_review.2026-08-05", "source.reader_audit", "reader_audit.schema", "Independent CAD reader audit is required.")
        self.require(bool((reader_audit.get("reviewer") or {}).get("derivation_id")), "source.reader_reviewer", "reader_audit.reviewer", "Reader audit needs an independent derivation ID.")
        for key in ("source_package_sha256", "review_sha256"):
            value = reader_audit.get(key)
            self.require(isinstance(value, str) and len(value) == 64, "source.reader_audit_hash", f"reader_audit.{key}", "Reader audit evidence needs a full SHA-256.")
        semantic_summary = self.data.get("semantic_summary") or {}
        self.require(semantic_summary.get("status") == "verified", "source.semantic_summary", "semantic_summary.status", "CAD semantic inventory is not verified.")
        self.require(not (semantic_summary.get("duplicate_qa_view_keys") or []), "source.semantic_duplicate", "semantic_summary.duplicate_qa_view_keys", "Semantic QA keys must be unique.")
        self.require(semantic_summary.get("all_titles_canonical") is True, "source.semantic_titles", "semantic_summary.all_titles_canonical", "Every model-driving view needs a canonical title.")
        self.require(semantic_summary.get("all_model_roles_resolved") is True, "source.semantic_roles", "semantic_summary.all_model_roles_resolved", "Every model-driving role must be resolved.")
        self.require(semantic_summary.get("all_elevations_and_sections_directional") is True, "source.semantic_directions", "semantic_summary.all_elevations_and_sections_directional", "Elevations and sections need stable direction keys.")
        units = self.data.get("units") or {}
        self.require(units.get("value") in {"mm", "cm", "m", "in", "ft"}, "source.units", "units.value", "Units are invalid.")
        self.require(units.get("basis") in {"cad_header", "user_confirmed"}, "source.units_basis", "units.basis", "Units need CAD-header or user evidence.")
        sources = self.data.get("sources") or []
        self.require(bool(sources), "source.empty", "sources", "At least one CAD source is required.")
        source_ids = _unique_ids(sources, "sources", self)
        for index, source in enumerate(sources):
            location = f"sources[{index}]"
            path = resolve_path(self.project, source.get("path"))
            expected = source.get("sha256")
            self.require(path is not None and path.is_file(), "source.file", f"{location}.path", "CAD source is missing.")
            self.require(bool(source.get("role")), "source.role", f"{location}.role", "Source role is required.")
            self.require(isinstance(expected, str) and len(expected) == 64, "source.hash", f"{location}.sha256", "A full source SHA-256 is required.")
            if path is not None and path.is_file() and isinstance(expected, str) and len(expected) == 64:
                self.require(sha256_file(path).lower() == expected.lower(), "source.stale", location, "CAD source fingerprint changed.")

        frames = self.data.get("frames") or []
        views = self.data.get("views") or []
        self.require(bool(frames), "source.frames", "frames", "At least one complete drawing frame is required.")
        frame_ids = _unique_ids(frames, "frames", self)
        view_ids = _unique_ids(views, "views", self)
        del view_ids
        for index, frame in enumerate(frames):
            location = f"frames[{index}]"
            self.require(frame.get("source_id") in source_ids, "source.frame_source", f"{location}.source_id", "Frame must link to an approved source.")
            self.require(frame.get("complete") is True, "source.frame_incomplete", f"{location}.complete", "Frame boundary or content is incomplete.")
            self.require(frame.get("verification_state") == "verified", "source.frame_unverified", f"{location}.verification_state", "Frame needs machine or review verification.")
            self.require(_valid_bounds(frame.get("bounds")), "source.frame_bounds", f"{location}.bounds", "Frame bounds must have positive area.")
            self._require_file(frame.get("preview_jpg"), "source.preview", f"{location}.preview_jpg")
            self._require_file(frame.get("vector_master"), "source.vector", f"{location}.vector_master")
            self._require_file(frame.get("render_manifest"), "source.render_manifest", f"{location}.render_manifest")
            self._require_file(frame.get("trace_bounds_svg"), "source.trace_svg", f"{location}.trace_bounds_svg")
            evidence_hashes = frame.get("evidence_sha256") or {}
            for key in ("preview_jpg", "vector_master", "render_manifest", "trace_bounds_svg"):
                self._require_hashed_file(frame.get(key), evidence_hashes.get(key), "source.evidence_hash", f"{location}.evidence_sha256.{key}")
            coverage = frame.get("render_coverage") or {}
            self.require(coverage.get("status") == "verified", "source.coverage", f"{location}.render_coverage.status", "CAD-to-render coverage is not verified.")
            self.require(coverage.get("vector_status") == "verified", "source.vector_coverage", f"{location}.render_coverage.vector_status", "Entity-to-render vector coverage is not verified.")
            self.require(coverage.get("coverage_ratio") == 1.0, "source.coverage_ratio", f"{location}.render_coverage.coverage_ratio", "Every source entity must produce render primitives.")
            self.require(not (coverage.get("missing_handles") or []), "source.missing_handles", f"{location}.render_coverage.missing_handles", "Source entities are missing from the render pipeline.")
            self.require(not (coverage.get("missing_entity_classes") or []), "source.coverage_missing", f"{location}.render_coverage.missing_entity_classes", "Rendered frame omits CAD entity classes.")
            edge_review = coverage.get("edge_review") or {}
            self.require(edge_review.get("status") == "verified", "source.frame_edge_review", f"{location}.render_coverage.edge_review.status", "Frame edge-touching entities need review.")
            self.require(not (edge_review.get("unexplained_handles") or []), "source.frame_edge_touch", f"{location}.render_coverage.edge_review.unexplained_handles", "Unexplained geometry touches the frame crop edge.")

        model_views = []
        qa_keys: set[str] = set()
        frame_bounds_seen: dict[str, set[tuple[float, float, float, float]]] = {}
        for index, view in enumerate(views):
            location = f"views[{index}]"
            role = view.get("role")
            self.require(view.get("frame_id") in frame_ids, "source.view_frame", f"{location}.frame_id", "View must link to a complete frame.")
            self.require(_valid_bounds(view.get("bounds")), "source.view_bounds", f"{location}.bounds", "View bounds must have positive area.")
            if role in MODEL_ROLES and view.get("include", True):
                model_views.append(view)
                title_evidence = view.get("title_evidence") or {}
                self.require(bool(title_evidence.get("canonical")), "source.canonical_title", f"{location}.title_evidence.canonical", "Canonical title evidence is required.")
                self.require(bool(title_evidence.get("fragments")), "source.title_fragments", f"{location}.title_evidence.fragments", "Canonical title must retain source text fragments.")
                self.require(view.get("segmentation_confidence") in {"high", "medium"}, "source.segmentation_confidence", f"{location}.segmentation_confidence", "View segmentation confidence is not deliverable.")
                self.require(not str(view.get("segmentation_method") or "").endswith("fallback"), "source.segmentation_fallback", f"{location}.segmentation_method", "Title-only segmentation fallback cannot pass.")
                semantic = view.get("semantic_index") or {}
                self.require(semantic.get("status") == "verified", "source.semantic_view", f"{location}.semantic_index.status", "View semantic inventory is not verified.")
                self.require(not (semantic.get("blocking_reasons") or []), "source.semantic_blocked", f"{location}.semantic_index.blocking_reasons", "View semantic blockers remain.")
                self.require(semantic.get("qa_view") == view.get("qa_view"), "source.semantic_qa_key", f"{location}.semantic_index.qa_view", "Semantic and contract QA keys disagree.")
                if role in {"elevation", "section"}:
                    direction = semantic.get("direction") or {}
                    self.require(direction.get("confidence") == "high" and bool(direction.get("key")), "source.view_direction", f"{location}.semantic_index.direction", "Elevation or section needs a high-confidence direction key.")
                self.require(view.get("complete") is True, "source.view_incomplete", f"{location}.complete", "Model-driving view is clipped or incomplete.")
                self.require(view.get("readable") is True, "source.view_unreadable", f"{location}.readable", "Model-driving annotations are not readable.")
                self.require(bool(view.get("qa_view")), "source.qa_view", f"{location}.qa_view", "Stable QA view key is required.")
                qa_key = str(view.get("qa_view") or "")
                self.require(qa_key not in qa_keys, "source.duplicate_qa_view", f"{location}.qa_view", "Each model-driving view needs a unique QA key.")
                qa_keys.add(qa_key)
                self.require(bool(view.get("source_entity_ids")), "source.entities", f"{location}.source_entity_ids", "View needs traceable source entities.")
                self._require_file(view.get("render_manifest"), "source.view_manifest", f"{location}.render_manifest")
                evidence_hashes = view.get("evidence_sha256") or {}
                for key in ("render_manifest", "trace_bounds_svg"):
                    self._require_hashed_file(view.get(key), evidence_hashes.get(key), "source.view_evidence_hash", f"{location}.evidence_sha256.{key}")
                vector_coverage = view.get("vector_coverage") or {}
                self.require(vector_coverage.get("status") == "verified", "source.view_vector_coverage", f"{location}.vector_coverage.status", "View entity-to-render coverage is not verified.")
                self.require(not (vector_coverage.get("missing_handles") or []), "source.view_missing_handles", f"{location}.vector_coverage.missing_handles", "View source entities are missing from the render pipeline.")
                edge_review = vector_coverage.get("edge_review") or {}
                self.require(edge_review.get("status") == "verified", "source.view_edge_review", f"{location}.vector_coverage.edge_review.status", "View edge-touching entities need review.")
                self.require(not (edge_review.get("unexplained_handles") or []), "source.view_edge_touch", f"{location}.vector_coverage.edge_review.unexplained_handles", "Unexplained geometry touches the view crop edge.")
                bounds = view.get("bounds") or {}
                signature = tuple(float(bounds.get(key, 0)) for key in ("xmin", "ymin", "xmax", "ymax"))
                seen = frame_bounds_seen.setdefault(str(view.get("frame_id")), set())
                self.require(signature not in seen, "source.collapsed_views", f"{location}.bounds", "Separate model-driving views in one frame cannot share the same bounds.")
                seen.add(signature)
        self.require(any(view.get("role") == "plan" for view in model_views), "source.no_plan", "views", "At least one complete plan is required.")
        required_roles = set(self.data.get("required_roles") or [])
        present_roles = {str(view.get("role")) for view in model_views}
        self.require(required_roles.issubset(present_roles), "source.required_roles", "required_roles", f"Missing required roles: {sorted(required_roles - present_roles)}")
        if {"elevation", "section"}.intersection(required_roles):
            self.require(any((view.get("semantic_index") or {}).get("levels") for view in model_views), "source.level_evidence", "views.semantic_index.levels", "At least one verified level datum is required for elevation/section modeling.")
        self.require(not (self.data.get("unresolved") or []), "source.unresolved", "unresolved", "Unresolved reading defects block verification.")

    def _validate_building_topology(self) -> None:
        self.require(
            any(item.get("kind") == "source-index" for item in self.data.get("upstream") or []),
            "topology.source_index",
            "upstream",
            "Topology must be bound to the verified source index.",
        )
        self.require(self.data.get("status") == "confirmed", "topology.status", "status", "Topology must be confirmed before build.")
        derivation = self.data.get("derivation") or {}
        topology_derivation_id = str(derivation.get("id") or "")
        self.require(bool(topology_derivation_id), "topology.derivation", "derivation.id", "Topology needs a stable derivation ID.")
        self.require(derivation.get("agent_role") == "topology_derivation", "topology.derivation_role", "derivation.agent_role", "Topology must identify the derivation role.")

        source_data: dict[str, Any] = {}
        source_reference = next((item for item in self.data.get("upstream") or [] if item.get("kind") == "source-index"), None)
        source_path = resolve_path(self.project, (source_reference or {}).get("path"))
        if source_path is not None and source_path.is_file():
            try:
                source_data = load_json(source_path)
            except (OSError, ValueError, TypeError):
                source_data = {}
        source_views = {
            str(view.get("id")): view
            for view in source_data.get("views") or []
            if view.get("role") in MODEL_ROLES and view.get("include", True)
        }
        self.require(bool(source_views), "topology.source_views", "upstream", "Verified source index has no model-driving views.")

        coordinate_system = self.data.get("coordinate_system") or {}
        self.require(coordinate_system.get("units") == "mm", "topology.units", "coordinate_system.units", "Topology coordinates must be normalized to millimetres.")
        self.require(_valid_point3(coordinate_system.get("origin")), "topology.origin", "coordinate_system.origin", "Topology needs a 3D project origin.")
        axes = [coordinate_system.get(key) for key in ("x_axis", "y_axis", "z_axis")]
        self.require(all(_valid_nonzero_vector3(axis) for axis in axes), "topology.axes", "coordinate_system", "Topology axes must be non-zero 3D vectors.")
        self.require(bool(coordinate_system.get("elevation_datum")), "topology.datum", "coordinate_system.elevation_datum", "Topology needs an explicit elevation datum.")

        registrations = self.data.get("registrations") or []
        registration_ids = _unique_ids(registrations, "registrations", self)
        del registration_ids
        registered_views: set[str] = set()
        for index, registration in enumerate(registrations):
            location = f"registrations[{index}]"
            source_view_id = str(registration.get("source_view_id") or "")
            source_view = source_views.get(source_view_id) or {}
            self.require(source_view_id in source_views, "topology.registration_view", f"{location}.source_view_id", "Registration references an unknown model-driving source view.")
            self.require(source_view_id not in registered_views, "topology.registration_duplicate", f"{location}.source_view_id", "Each source view may be registered only once.")
            registered_views.add(source_view_id)
            self.require(registration.get("role") == source_view.get("role"), "topology.registration_role", f"{location}.role", "Registration role disagrees with source-index.")
            self.require(registration.get("qa_view") == source_view.get("qa_view"), "topology.registration_qa_view", f"{location}.qa_view", "Registration QA key disagrees with source-index.")
            self.require(registration.get("status") == "verified", "topology.registration_status", f"{location}.status", "Registration is not verified.")
            transform = registration.get("transform") or {}
            self.require(_valid_point(transform.get("source_origin")), "topology.registration_source_origin", f"{location}.transform.source_origin", "Registered view needs its source-CAD origin for reverse projection.")
            self.require(_valid_point3(transform.get("origin")), "topology.registration_origin", f"{location}.transform.origin", "Registered view needs a 3D origin.")
            self.require(_valid_nonzero_vector3(transform.get("x_axis")) and _valid_nonzero_vector3(transform.get("y_axis")), "topology.registration_axes", f"{location}.transform", "Registered view needs non-zero local axes.")
            self.require(isinstance(transform.get("scale"), (int, float)) and transform.get("scale", 0) > 0, "topology.registration_scale", f"{location}.transform.scale", "Registered view scale must be positive.")
            anchors = registration.get("anchor_evidence") or []
            self.require(len(anchors) >= 2, "topology.registration_anchors", f"{location}.anchor_evidence", "Every view registration needs at least two asymmetric anchors.")
        self.require(set(source_views).issubset(registered_views), "topology.registration_coverage", "registrations", f"Unregistered model-driving views: {sorted(set(source_views) - registered_views)}")

        def validate_evidence(records: Any, location: str, minimum: int = 1) -> list[dict[str, Any]]:
            evidence = records if isinstance(records, list) else []
            self.require(len(evidence) >= minimum, "topology.evidence", location, "Traceable source evidence is required.")
            for evidence_index, item in enumerate(evidence):
                item_location = f"{location}[{evidence_index}]"
                if not isinstance(item, dict):
                    self.require(False, "topology.evidence_record", item_location, "Evidence records must be structured objects.")
                    continue
                self.require(item.get("source_view_id") in source_views, "topology.evidence_view", f"{item_location}.source_view_id", "Evidence references an unknown model-driving view.")
                self.require(item.get("kind") in GEOMETRY_EVIDENCE | {"cad_annotation"}, "topology.evidence_kind", f"{item_location}.kind", "Evidence kind is invalid.")
                self.require(bool(item.get("source_entity_ids")), "topology.evidence_entities", f"{item_location}.source_entity_ids", "Evidence must retain CAD source entity IDs.")
            return evidence

        audit = self.data.get("topology_audit") or {}
        self.require(audit.get("schema") == "cad_to_sketchup.topology_review.2026-08-05", "topology.audit_schema", "topology_audit.schema", "Independent topology review is required.")
        self.require(audit.get("status") == "verified", "topology.audit_status", "topology_audit.status", "Independent topology review has not passed.")
        self._require_hashed_file(audit.get("candidate_path"), audit.get("candidate_sha256"), "topology.audit_candidate", "topology_audit.candidate_sha256")
        self._require_hashed_file(audit.get("review_path"), audit.get("review_sha256"), "topology.audit_review", "topology_audit.review_sha256")
        reviewer = audit.get("reviewer") or {}
        self.require(bool(reviewer.get("id")) and bool(reviewer.get("derivation_id")), "topology.audit_reviewer", "topology_audit.reviewer", "Independent topology reviewer identity is required.")
        self.require(audit.get("topology_derivation_id") == topology_derivation_id, "topology.audit_derivation", "topology_audit.topology_derivation_id", "Audit is not bound to this topology derivation.")
        self.require(reviewer.get("derivation_id") != topology_derivation_id, "topology.audit_independence", "topology_audit.reviewer.derivation_id", "Topology derivation and review must use different derivations.")
        for key in TOPOLOGY_AUDIT_CHECKS:
            self.require((audit.get("checks") or {}).get(key) is True, f"topology.audit_{key}", f"topology_audit.checks.{key}", "Independent topology audit check failed.")
        self.require(audit.get("confirmation_boundary_presented") is True, "topology.confirmation_boundary", "topology_audit.confirmation_boundary_presented", "The topology review boundary was not presented before user confirmation.")

        white_model = self.data.get("white_model") or {}
        self.require(white_model.get("stage") == "topology_white_model", "topology.white_model_stage", "white_model.stage", "Artifact must be identified as the topology white-model stage.")
        self._require_hashed_file(white_model.get("model_path"), white_model.get("model_sha256"), "topology.white_model", "white_model.model_sha256")
        review_view_ids: set[str] = set()
        for index, view in enumerate(white_model.get("review_views") or []):
            location = f"white_model.review_views[{index}]"
            view_id = str(view.get("source_view_id") or "")
            self.require(view_id in source_views, "topology.white_model_view", f"{location}.source_view_id", "White-model review view must bind to source-index.")
            self.require(view.get("role") == source_views.get(view_id, {}).get("role"), "topology.white_model_role", f"{location}.role", "White-model review role disagrees with source-index.")
            self._require_hashed_file(view.get("path"), view.get("sha256"), "topology.white_model_evidence", f"{location}.sha256")
            review_view_ids.add(view_id)
        self.require(set(source_views).issubset(review_view_ids), "topology.white_model_coverage", "white_model.review_views", f"White model lacks review views for: {sorted(set(source_views) - review_view_ids)}")

        confirmation = self.data.get("user_confirmation") or {}
        self.require(confirmation.get("confirmed") is True, "topology.user_confirmation", "user_confirmation.confirmed", "User must confirm the topology white model.")
        self.require(bool(confirmation.get("confirmation_id")) and bool(confirmation.get("instruction")), "topology.confirmation_record", "user_confirmation", "Record the explicit user confirmation instruction and ID.")
        self.require(confirmation.get("model_sha256") == white_model.get("model_sha256"), "topology.confirmation_model", "user_confirmation.model_sha256", "User confirmation is not bound to the current white model.")
        self.require(confirmation.get("candidate_sha256") == audit.get("candidate_sha256"), "topology.confirmation_candidate", "user_confirmation.candidate_sha256", "User confirmation is not bound to the reviewed topology candidate.")
        control = self.data.get("control_basis") or {}
        self.require(control.get("priority") == ["user_confirmation", "control_mass", "cad_measurement", "documented_default"], "topology.priority", "control_basis.priority", "Control authority priority is not explicit.")
        control_mass = control.get("control_mass") or {}
        if control_mass.get("provided") is True:
            self.require(control_mass.get("scope") == "envelope_only", "topology.control_mass_scope", "control_basis.control_mass.scope", "A control mass may control only the basic envelope.")
            self._require_hashed_file(control_mass.get("path"), control_mass.get("sha256"), "topology.control_mass", "control_basis.control_mass.sha256")
        else:
            self.require(control_mass.get("scope") == "none", "topology.control_mass_scope", "control_basis.control_mass.scope", "Absent control mass must use scope 'none'.")

        levels = self.data.get("levels") or []
        level_ids = _unique_ids(levels, "levels", self)
        self.require(bool(levels), "topology.levels", "levels", "At least one level is required.")
        footprints: dict[str, list[list[float]]] = {}
        wall_ids: set[str] = set()
        topology_ids: set[str] = set(level_ids)
        for index, level in enumerate(levels):
            location = f"levels[{index}]"
            footprint = level.get("footprint") or []
            footprints[str(level.get("id"))] = footprint
            self.require(_valid_polygon(footprint), "topology.footprint", f"{location}.footprint", "Floor footprint must be a simple non-zero polygon.")
            validate_evidence(level.get("evidence"), f"{location}.evidence")
            self.require(level.get("top_elevation_mm", 0) > level.get("elevation_mm", 0), "topology.level_height", location, "Level top must be above its base.")
            ring = level.get("exterior_wall_ring") or {}
            self.require(ring.get("closed") is True, "topology.wall_ring_closed", f"{location}.exterior_wall_ring.closed", "Exterior wall ring must be closed.")
            self.require(ring.get("continuous_group") is True, "topology.wall_ring_group", f"{location}.exterior_wall_ring.continuous_group", "Each level exterior wall ring must be one continuous group.")
            level_wall_ids = [str(item) for item in ring.get("wall_ids") or [] if item]
            self.require(bool(level_wall_ids), "topology.wall_ids", f"{location}.exterior_wall_ring.wall_ids", "Exterior wall ring needs stable wall IDs for hosted elements.")
            self.require(not wall_ids.intersection(level_wall_ids), "topology.wall_ids", f"{location}.exterior_wall_ring.wall_ids", "Wall IDs must be globally unique.")
            wall_ids.update(level_wall_ids)

        walls = self.data.get("walls") or []
        declared_wall_ids = _unique_ids(walls, "walls", self)
        topology_ids.update(declared_wall_ids)
        wall_by_id = {str(wall.get("id")): wall for wall in walls if wall.get("id")}
        level_by_id = {str(level.get("id")): level for level in levels if level.get("id")}
        self.require(wall_ids == declared_wall_ids, "topology.wall_coverage", "walls", f"Wall-ring IDs and declared walls differ: missing={sorted(wall_ids - declared_wall_ids)}, extra={sorted(declared_wall_ids - wall_ids)}")
        for index, wall in enumerate(walls):
            location = f"walls[{index}]"
            self.require(wall.get("level_id") in level_ids, "topology.wall_level", f"{location}.level_id", "Wall references an unknown level.")
            self.require(_valid_point(wall.get("start")) and _valid_point(wall.get("end")) and wall.get("start") != wall.get("end"), "topology.wall_segment", location, "Wall needs distinct start and end points.")
            self.require(isinstance(wall.get("thickness_mm"), (int, float)) and wall.get("thickness_mm", 0) > 0, "topology.wall_thickness", f"{location}.thickness_mm", "Wall thickness must be positive.")
            self.require(wall.get("owns_exterior_face") is True, "topology.wall_ownership", f"{location}.owns_exterior_face", "Exterior wall must own the exterior face to prevent duplicates.")
            validate_evidence(wall.get("evidence"), f"{location}.evidence")
            profile = wall.get("top_profile")
            if profile is not None:
                start = wall.get("start") or [0, 0]
                end = wall.get("end") or [0, 0]
                length = ((float(end[0]) - float(start[0])) ** 2 + (float(end[1]) - float(start[1])) ** 2) ** 0.5
                valid = isinstance(profile, list) and len(profile) >= 2 and all(
                    isinstance(point, list) and len(point) == 2 and all(isinstance(value, (int, float)) for value in point)
                    for point in profile
                )
                self.require(valid, "topology.wall_top_profile", f"{location}.top_profile", "Wall top profile must contain [station_mm, elevation_mm] points.")
                if valid:
                    stations = [float(point[0]) for point in profile]
                    elevations = [float(point[1]) for point in profile]
                    level_top = float((level_by_id.get(str(wall.get("level_id"))) or {}).get("top_elevation_mm", 0))
                    self.require(abs(stations[0]) <= 0.1 and abs(stations[-1] - length) <= 0.1, "topology.wall_top_profile_extent", f"{location}.top_profile", "Wall top profile must span the full directed wall length from station 0 to the wall length.")
                    self.require(all(right - left > 0.1 for left, right in zip(stations, stations[1:])), "topology.wall_top_profile_order", f"{location}.top_profile", "Wall top-profile stations must be strictly increasing.")
                    level_base = float((level_by_id.get(str(wall.get("level_id"))) or {}).get("elevation_mm", 0))
                    # A measured sloping envelope can cross the nominal eave datum.
                    self.require(all(elevation > level_base + 0.1 for elevation in elevations), "topology.wall_top_profile_height", f"{location}.top_profile", "Wall top profile must remain above the owning level base.")
                    profile_evidence = [item for item in wall.get("evidence") or [] if item.get("kind") in {"measured_elevation", "measured_section"}]
                    self.require(bool(profile_evidence), "topology.wall_top_profile_evidence", f"{location}.evidence", "A non-flat wall top profile requires measured elevation or section evidence.")
        for index, level in enumerate(levels):
            ids = [str(item) for item in (level.get("exterior_wall_ring") or {}).get("wall_ids") or []]
            segments = [wall_by_id.get(item) or {} for item in ids]
            contiguous = bool(segments) and all(_same_point(segments[i].get("end"), segments[(i + 1) % len(segments)].get("start")) for i in range(len(segments)))
            self.require(contiguous, "topology.wall_ring_geometry", f"levels[{index}].exterior_wall_ring.wall_ids", "Wall segments do not form one ordered closed ring.")

        internal_walls = self.data.get("internal_walls") or []
        topology_ids.update(_unique_ids(internal_walls, "internal_walls", self))
        for index, wall in enumerate(internal_walls):
            location = f"internal_walls[{index}]"
            self.require(wall.get("level_id") in level_ids, "topology.internal_wall_level", f"{location}.level_id", "Internal wall references an unknown level.")
            self.require(_valid_point(wall.get("start")) and _valid_point(wall.get("end")) and wall.get("start") != wall.get("end"), "topology.internal_wall_segment", location, "Internal wall needs distinct start and end points.")
            self.require(isinstance(wall.get("thickness_mm"), (int, float)) and wall.get("thickness_mm", 0) > 0, "topology.internal_wall_thickness", f"{location}.thickness_mm", "Internal wall thickness must be positive.")
            self.require(isinstance(wall.get("base_elevation_mm"), (int, float)) and isinstance(wall.get("top_elevation_mm"), (int, float)) and wall.get("top_elevation_mm", 0) > wall.get("base_elevation_mm", 0), "topology.internal_wall_height", location, "Internal wall top must be above its base.")
            self.require(wall.get("vertical_extent_basis") in {"plan_height_annotation", "upper_cut_presence", "upper_cut_absence_plus_room_height"}, "topology.internal_wall_height_basis", f"{location}.vertical_extent_basis", "Internal wall vertical extent needs an explicit CAD basis.")
            validate_evidence(wall.get("evidence"), f"{location}.evidence")
            for endpoint_name in ("start", "end"):
                termination = wall.get(f"{endpoint_name}_termination")
                endpoint = wall.get(endpoint_name)
                nearby_exterior = [
                    host for host in walls
                    if host.get("level_id") == wall.get("level_id")
                    and _point_segment_distance(endpoint, host.get("start"), host.get("end")) <= float(host.get("thickness_mm", 0)) + 0.2
                ]
                self.require(not nearby_exterior or termination is not None, "topology.internal_wall_termination_missing", f"{location}.{endpoint_name}_termination", "An interior-wall endpoint within the exterior-wall depth must explicitly terminate at the exterior inner face.")
                if termination is None:
                    continue
                host_id = str(termination.get("host_wall_id") or "")
                host = wall_by_id.get(host_id)
                self.require(termination.get("rule") == "stop_at_exterior_inner_face", "topology.internal_wall_termination_rule", f"{location}.{endpoint_name}_termination.rule", "Interior-wall exterior termination must stop at the exterior wall inner face.")
                self.require(host is not None and host.get("level_id") == wall.get("level_id"), "topology.internal_wall_termination_host", f"{location}.{endpoint_name}_termination.host_wall_id", "Interior-wall termination references an unknown exterior wall on this level.")
                if host is not None:
                    footprint = footprints.get(str(wall.get("level_id"))) or []
                    self.require(_point_on_exterior_inner_face(wall.get(endpoint_name), host, footprint), "topology.internal_wall_termination_position", f"{location}.{endpoint_name}", "Interior-wall endpoint must lie on the cited exterior wall inner face, not its outer face.")

        slabs = self.data.get("slabs") or []
        topology_ids.update(_unique_ids(slabs, "slabs", self))
        for index, slab in enumerate(slabs):
            location = f"slabs[{index}]"
            level_id = str(slab.get("level_id"))
            self.require(level_id in level_ids, "topology.slab_level", f"{location}.level_id", "Slab references an unknown level.")
            boundary = slab.get("boundary") or []
            self.require(_valid_polygon(boundary), "topology.slab_boundary", f"{location}.boundary", "Slab boundary must be a simple polygon.")
            self.require(isinstance(slab.get("top_elevation_mm"), (int, float)), "topology.slab_elevation", f"{location}.top_elevation_mm", "Slab top elevation is required.")
            self.require(isinstance(slab.get("thickness_mm"), (int, float)) and slab.get("thickness_mm", 0) > 0, "topology.slab_thickness", f"{location}.thickness_mm", "Slab thickness must be measured and positive.")
            validate_evidence(slab.get("evidence"), f"{location}.evidence")
            inside = level_id in footprints and _polygon_contained(boundary, footprints[level_id])
            self.require(inside, "topology.slab_outside", f"{location}.boundary", "Slab protrudes beyond the owning floor envelope.")

        roofs = self.data.get("roofs") or []
        topology_ids.update(_unique_ids(roofs, "roofs", self))
        if any(view.get("role") == "roof_plan" for view in source_views.values()):
            self.require(bool(roofs), "topology.roof_missing", "roofs", "A verified roof plan requires roof topology.")
        for index, roof in enumerate(roofs):
            location = f"roofs[{index}]"
            self.require(roof.get("base_level_id") in level_ids, "topology.roof_level", f"{location}.base_level_id", "Roof references an unknown base level.")
            self.require(_valid_polygon(roof.get("boundary") or []), "topology.roof_boundary", f"{location}.boundary", "Roof boundary must be a simple polygon.")
            self.require(isinstance(roof.get("base_elevation_mm"), (int, float)), "topology.roof_elevation", f"{location}.base_elevation_mm", "Roof base elevation is required.")
            self.require(isinstance(roof.get("thickness_mm"), (int, float)) and roof.get("thickness_mm", 0) > 0, "topology.roof_thickness", f"{location}.thickness_mm", "Roof thickness must be measured and positive.")
            if roof.get("topology_type") in {"pitched", "compound"}:
                shell_faces = roof.get("shell_faces") or []
                self.require(bool(shell_faces) and all(len(face) >= 3 and all(_valid_point3(point) for point in face) for face in shell_faces), "topology.roof_shell", f"{location}.shell_faces", "Pitched or compound roof needs explicit 3D shell faces.")
            validate_evidence(roof.get("evidence"), f"{location}.evidence")

        roof_lights = self.data.get("roof_lights") or []
        topology_ids.update(_unique_ids(roof_lights, "roof_lights", self))
        roof_ids = {str(item.get("id") or "") for item in roofs}
        for index, roof_light in enumerate(roof_lights):
            location = f"roof_lights[{index}]"
            self.require(str(roof_light.get("host_roof_id") or "") in roof_ids,
                         "topology.roof_light_host", f"{location}.host_roof_id",
                         "Roof light references an unknown host roof.")
            boundary = roof_light.get("boundary") or []
            self.require(len(boundary) >= 3 and all(_valid_point3(point) for point in boundary),
                         "topology.roof_light_boundary", f"{location}.boundary",
                         "Roof light needs a measured 3D boundary on its host roof.")
            validate_evidence(roof_light.get("evidence"), f"{location}.evidence")

        openings = self.data.get("openings") or []
        topology_ids.update(_unique_ids(openings, "openings", self))
        for index, opening in enumerate(openings):
            location = f"openings[{index}]"
            self.require(opening.get("level_id") in level_ids, "topology.opening_level", f"{location}.level_id", "Opening references an unknown level.")
            self.require(opening.get("host_wall_id") in wall_ids, "topology.opening_host", f"{location}.host_wall_id", "Opening must identify a real host wall from the exterior ring.")
            self.require(isinstance(opening.get("width_mm"), (int, float)) and opening.get("width_mm", 0) > 0, "topology.opening_size", f"{location}.width_mm", "Opening width must be positive.")
            self.require(isinstance(opening.get("height_mm"), (int, float)) and opening.get("height_mm", 0) > 0, "topology.opening_size", f"{location}.height_mm", "Opening height must be positive.")
            self.require(_valid_point(opening.get("position")), "topology.opening_position", f"{location}.position", "Opening needs a plan-registered position.")
            host_wall = wall_by_id.get(str(opening.get("host_wall_id"))) or {}
            self.require(_point_on_segment(opening.get("position"), host_wall.get("start"), host_wall.get("end")), "topology.opening_on_host", f"{location}.position", "Opening plan position must lie on its host wall segment.")
            self.require(opening.get("through_cut") is True, "topology.opening_through", f"{location}.through_cut", "Door/window openings must cut fully through the host wall.")
            opening_evidence = validate_evidence(opening.get("evidence"), f"{location}.evidence", minimum=2)
            opening_roles = {source_views.get(str(item.get("source_view_id")), {}).get("role") for item in opening_evidence}
            self.require("plan" in opening_roles and "elevation" in opening_roles, "topology.opening_cross_view", f"{location}.evidence", "Opening needs both plan-host and elevation-size evidence.")
            wall = opening.get("wall_thickness_mm")
            cut = opening.get("cut_depth_mm")
            self.require(isinstance(cut, (int, float)) and cut > 0, "topology.opening_depth", f"{location}.cut_depth_mm", "Opening cut depth is required.")
            if isinstance(wall, (int, float)) and wall > 0:
                self.require(cut >= wall, "topology.opening_depth", f"{location}.cut_depth_mm", "Cut depth must penetrate the actual wall thickness.")
                self.require(opening.get("depth_basis") == "measured_wall_thickness", "topology.opening_depth_basis", f"{location}.depth_basis", "Measured wall thickness must be recorded as the cut-depth basis.")
            else:
                self.require(cut == 200, "topology.opening_default_depth", f"{location}.cut_depth_mm", "When wall thickness is absent, ordinary openings use the confirmed 200 mm default.")
                self.require(opening.get("depth_basis") == "default_200_missing_wall_thickness", "topology.opening_depth_basis", f"{location}.depth_basis", "Record why the 200 mm default was used.")

        curtain_walls = self.data.get("curtain_walls") or []
        topology_ids.update(_unique_ids(curtain_walls, "curtain_walls", self))
        for index, curtain in enumerate(curtain_walls):
            location = f"curtain_walls[{index}]"
            self.require(set(curtain.get("level_ids") or []).issubset(level_ids), "topology.curtain_levels", f"{location}.level_ids", "Curtain wall references unknown levels.")
            boundary = curtain.get("boundary") or []
            self.require(len(boundary) >= 3 and all(_valid_point3(point) for point in boundary), "topology.curtain_boundary", f"{location}.boundary", "Curtain wall needs a valid 3D boundary.")
            curtain_evidence = validate_evidence(curtain.get("evidence"), f"{location}.evidence", minimum=2)
            roles = {source_views.get(str(item.get("source_view_id")), {}).get("role") for item in curtain_evidence}
            self.require("plan" in roles and "elevation" in roles, "topology.curtain_cross_view", f"{location}.evidence", "Curtain wall needs plan and elevation evidence.")

        sweeps = self.data.get("sweeps") or []
        topology_ids.update(_unique_ids(sweeps, "sweeps", self))
        for index, sweep in enumerate(sweeps):
            location = f"sweeps[{index}]"
            self.require(sweep.get("path_continuous") is True, "topology.sweep_path", f"{location}.path_continuous", "Molding/cornice path must remain continuous through corners.")
            self.require(len(sweep.get("path") or []) >= 2, "topology.sweep_path", f"{location}.path", "Sweep needs a traceable path.")
            self.require(_valid_polygon(sweep.get("profile") or []), "topology.sweep_profile", f"{location}.profile", "Sweep needs a complete non-zero section profile.")
            basis = sweep.get("profile_basis") or {}
            self.require(_valid_nonzero_vector3(basis.get("u_axis")), "topology.sweep_basis", f"{location}.profile_basis.u_axis", "Sweep profile needs a non-zero U axis.")
            self.require(_valid_nonzero_vector3(basis.get("v_axis")), "topology.sweep_basis", f"{location}.profile_basis.v_axis", "Sweep profile needs a non-zero V axis.")
            if _valid_nonzero_vector3(basis.get("u_axis")) and _valid_nonzero_vector3(basis.get("v_axis")):
                u = basis["u_axis"]
                v = basis["v_axis"]
                cross_length = sum((u[(axis + 1) % 3] * v[(axis + 2) % 3] - u[(axis + 2) % 3] * v[(axis + 1) % 3]) ** 2 for axis in range(3)) ** 0.5
                self.require(cross_length > 1e-9, "topology.sweep_basis_parallel", f"{location}.profile_basis", "Sweep profile axes cannot be parallel.")
            profile_evidence = sweep.get("profile_evidence") or {}
            self.require(profile_evidence.get("kind") in GEOMETRY_EVIDENCE, "topology.sweep_profile_evidence", f"{location}.profile_evidence.kind", "Sweep profile needs measurable section or end-profile evidence.")
            self.require(sweep.get("corner_policy") == "continuous_turn", "topology.sweep_corner", f"{location}.corner_policy", "Facade sweeps cannot stop at corners unless termination evidence exists.")
            self.require(bool(sweep.get("datum_evidence")), "topology.sweep_datum", f"{location}.datum_evidence", "Sweep datum evidence is required.")
            validate_evidence(sweep.get("datum_evidence"), f"{location}.datum_evidence")
            sweep_evidence = validate_evidence(sweep.get("evidence"), f"{location}.evidence")
            elevation_views = {item.get("source_view_id") for item in sweep_evidence if source_views.get(str(item.get("source_view_id")), {}).get("role") == "elevation"}
            if len(sweep.get("covered_facades") or []) > 1:
                self.require(len(elevation_views) >= 2, "topology.sweep_cross_view", f"{location}.evidence", "A corner-turning sweep needs evidence from at least two facade views.")
            self.require(sweep.get("termination_policy") in {"closed_loop", "evidence_bound_ends"}, "topology.sweep_termination", f"{location}.termination_policy", "Sweep termination policy is missing.")

        canopies = self.data.get("canopies") or []
        topology_ids.update(_unique_ids(canopies, "canopies", self))
        for index, canopy in enumerate(canopies):
            location = f"canopies[{index}]"
            orientations = set(canopy.get("orthogonal_elevation_views") or [])
            resolution = canopy.get("plan_elevation_resolution") or {}
            resolved_by_user = resolution.get("policy") == "user_confirmed_plan_xy_elevation_z"
            if resolved_by_user:
                self._require_hashed_file(resolution.get("path"), resolution.get("sha256"), "topology.canopy_resolution", f"{location}.plan_elevation_resolution")
            else:
                self.require(len(orientations) >= 2, "topology.canopy_cross_view", f"{location}.orthogonal_elevation_views", "Canopy thickness and edge form require two orthogonal elevations, or an explicit hash-bound user plan/elevation resolution.")
            self.require(canopy.get("closed_volume") is True, "topology.canopy_volume", f"{location}.closed_volume", "Canopy must be a closed 3D volume.")
            evidence = canopy.get("thickness_evidence") or {}
            self.require(evidence.get("kind") in GEOMETRY_EVIDENCE, "topology.canopy_thickness", f"{location}.thickness_evidence.kind", "Canopy thickness cannot come from a default or visual guess.")
            self.require(canopy.get("host_wall_id") in wall_ids, "topology.canopy_host", f"{location}.host_wall_id", "Canopy must identify its facade host wall.")
            self.require(_valid_polygon(canopy.get("footprint") or []), "topology.canopy_footprint", f"{location}.footprint", "Canopy needs a measurable plan footprint.")
            self.require(isinstance(canopy.get("thickness_mm"), (int, float)) and canopy.get("thickness_mm", 0) > 0, "topology.canopy_size", f"{location}.thickness_mm", "Canopy thickness must be measured and positive.")
            canopy_evidence = validate_evidence(canopy.get("evidence"), f"{location}.evidence", minimum=2)
            canopy_elevations = {item.get("source_view_id") for item in canopy_evidence if source_views.get(str(item.get("source_view_id")), {}).get("role") == "elevation"}
            if resolved_by_user:
                plan_views = {item.get("source_view_id") for item in canopy_evidence if source_views.get(str(item.get("source_view_id")), {}).get("role") in {"plan", "roof_plan"}}
                self.require(bool(plan_views) and bool(canopy_elevations), "topology.canopy_evidence", f"{location}.evidence", "User-resolved canopy still needs measured plan and elevation evidence.")
            else:
                self.require(len(canopy_elevations) >= 2, "topology.canopy_evidence", f"{location}.evidence", "Canopy topology needs evidence from at least two elevation views.")

        from access_geometry import mesh_errors
        access = self.data.get("access_elements") or []
        topology_ids.update(_unique_ids(access, "access_elements", self))
        for index, entry in enumerate(access):
            location = f"access_elements[{index}]"
            self.require(entry.get("host_wall_id") in wall_ids, "topology.access_host", location, "Entrance needs a real host wall.")
            validate_evidence(entry.get("evidence"), f"{location}.evidence", minimum=2)
            for message in mesh_errors(entry):
                self.require(False, "topology.access_mesh", location, message)

        columns = self.data.get("columns") or []
        topology_ids.update(_unique_ids(columns, "columns", self))
        for index, column in enumerate(columns):
            location = f"columns[{index}]"
            self.require(column.get("level_id") in level_ids, "topology.column_level", location, "Column needs a real level.")
            self.require(column.get("top_elevation_mm", 0) > column.get("base_elevation_mm", 0), "topology.column_height", location, "Column height must be positive.")
            validate_evidence(column.get("evidence"), f"{location}.evidence", minimum=2)

        materials = self.data.get("materials") or []
        topology_ids.update(_unique_ids(materials, "materials", self))
        for index, material in enumerate(materials):
            location = f"materials[{index}]"
            if material.get("geometric_effect") is True:
                evidence = material.get("evidence") or {}
                self.require(evidence.get("kind") in GEOMETRY_EVIDENCE, "topology.material_geometry", f"{location}.evidence.kind", "Material text/color may change geometry only with measurable geometric evidence.")
        for index, registration in enumerate(registrations):
            visible_ids = set(str(item) for item in registration.get("visible_topology_ids") or [])
            self.require(bool(visible_ids), "topology.registration_visible_scope", f"registrations[{index}].visible_topology_ids", "Every registered view needs a non-empty visible topology scope.")
            self.require(visible_ids.issubset(topology_ids), "topology.registration_unknown_topology", f"registrations[{index}].visible_topology_ids", f"Registered view references unknown topology IDs: {sorted(visible_ids - topology_ids)}")
        self.require(not (self.data.get("unresolved") or []), "topology.unresolved", "unresolved", "Unresolved model-driving topology blocks confirmation.")

    def _validate_sketchup_build(self) -> None:
        topology_reference = next((item for item in self.data.get("upstream") or [] if item.get("kind") == "building-topology"), None)
        self.require(
            topology_reference is not None,
            "build.topology",
            "upstream",
            "SketchUp build must be bound to the confirmed topology contract.",
        )
        self.require(self.data.get("status") == "verified", "build.status", "status", "SketchUp build contract must be verified.")
        topology = None
        topology_path = resolve_path(self.project, (topology_reference or {}).get("path"))
        if topology_path is not None and topology_path.is_file():
            try:
                topology = load_json(topology_path)
            except (OSError, ValueError, TypeError):
                topology = None

        for field in ("build_plan", "execution_report"):
            artifact = self.data.get(field) or {}
            self._require_hashed_file(artifact.get("path"), artifact.get("sha256"), f"build.{field}", field)
        model = self.data.get("active_model") or {}
        model_path = resolve_path(self.project, model.get("path"))
        self.require(model_path is not None and model_path.is_file(), "build.model", "active_model.path", "Active SKP is missing.")
        expected = model.get("sha256")
        self.require(isinstance(expected, str) and len(expected) == 64, "build.model_hash", "active_model.sha256", "Active SKP needs a full SHA-256.")
        if model_path is not None and model_path.is_file() and isinstance(expected, str) and len(expected) == 64:
            self.require(sha256_file(model_path).lower() == expected.lower(), "build.model_stale", "active_model", "Active SKP changed after build verification.")

        required_ids = set(self.data.get("required_topology_ids") or [])
        if topology:
            expected_ids = {
                str(item.get("id"))
                for key in ("levels", "walls", "internal_walls", "slabs", "roofs", "roof_lights", "openings", "curtain_walls", "sweeps", "canopies", "materials", "access_elements", "columns")
                for item in topology.get(key) or []
            }
            self.require(required_ids == expected_ids, "build.topology_coverage", "required_topology_ids", f"Build scope must equal confirmed topology IDs; missing={sorted(expected_ids - required_ids)} extra={sorted(required_ids - expected_ids)}")
        results = self.data.get("element_results") or []
        result_ids = _unique_ids(results, "element_results", self, key="topology_id")
        self.require(bool(required_ids), "build.required_ids", "required_topology_ids", "Build must declare every topology element it is responsible for.")
        self.require(required_ids == result_ids, "build.coverage", "element_results", f"Build results must exactly cover topology IDs; missing={sorted(required_ids - result_ids)} extra={sorted(result_ids - required_ids)}")
        for index, result in enumerate(results):
            location = f"element_results[{index}]"
            self.require(result.get("status") == "PASS", "build.element", f"{location}.status", "A topology element failed to build or verify.")
            entity_ids = result.get("sketchup_entity_ids") or []
            self.require(bool(entity_ids) and all(isinstance(item, int) and item > 0 for item in entity_ids), "build.entity", f"{location}.sketchup_entity_ids", "Traceable SketchUp persistent entity IDs are required.")
            self.require(result.get("failed_solids") == 0, "build.failed_geometry", f"{location}.failed_solids", "Failed geometry cannot be counted as a successful element.")
        statistics = self.data.get("statistics") or {}
        self.require(statistics.get("unique_semantic_inputs") == len(required_ids), "build.statistics_inputs", "statistics.unique_semantic_inputs", "Generator statistics must equal unique topology inputs.")
        self.require(statistics.get("failed_solids") == 0, "build.statistics_failures", "statistics.failed_solids", "Generator reported failed solids.")
        expected_openings = len((topology or {}).get("openings") or [])
        self.require(statistics.get("unique_openings") == expected_openings, "build.statistics_openings", "statistics.unique_openings", "Opening statistics do not match confirmed topology.")
        checks = self.data.get("geometry_checks") or {}
        for key, message in {
            "complete_element_coverage": "Not every confirmed topology element has a traceable result.",
            "continuous_wall_groups": "Exterior wall rings are fragmented.",
            "slabs_contained": "A slab protrudes beyond its envelope.",
            "true_openings": "Openings are not true through-cuts.",
            "opening_assemblies_complete": "Opening frame/panel assemblies are incomplete.",
            "curtain_wall_systems_complete": "Curtain-wall frames or grids are incomplete.",
            "corner_continuity": "Moldings/cornices break at corners.",
            "no_duplicate_exterior_faces": "Duplicate exterior wall faces remain.",
            "canopies_complete": "Canopy topology is incomplete.",
            "materials_traceable": "Material assignments are not traceable to CAD evidence and topology IDs.",
            "debug_geometry_clear": "Visible cutter or debug geometry remains.",
        }.items():
            self.require(checks.get(key) is True, f"build.{key}", f"geometry_checks.{key}", message)
        self.require(not (self.data.get("unresolved") or []), "build.unresolved", "unresolved", "Unresolved build defects block QA.")

    def _validate_independent_qa(self) -> None:
        upstream_kinds = {item.get("kind") for item in self.data.get("upstream") or []}
        self.require(
            {"source-index", "building-topology", "sketchup-build", "independent-qa-derivation"}.issubset(upstream_kinds),
            "qa.upstream",
            "upstream",
            "Independent QA must bind source, topology, active build, and independent CAD derivation.",
        )
        self.require(self.data.get("status") == "PASS", "qa.status", "status", "Independent QA must pass.")
        cad = self.data.get("cad_provenance") or {}
        model = self.data.get("model_provenance") or {}
        self.require(bool(cad.get("derivation_id")), "qa.cad_derivation", "cad_provenance.derivation_id", "CAD derivation ID is required.")
        self.require(bool(model.get("derivation_id")), "qa.model_derivation", "model_provenance.derivation_id", "Model derivation ID is required.")
        self.require(cad.get("derivation_id") != model.get("derivation_id"), "qa.same_derivation", "provenance", "CAD and model QA must use independent derivations.")
        reviewer_id = str((self.data.get("independent_review") or {}).get("reviewer_derivation_id") or "")
        self.require(bool(reviewer_id), "qa.review_derivation", "independent_review.reviewer_derivation_id", "Independent reviewer derivation ID is required.")
        self.require(reviewer_id not in {cad.get("derivation_id"), model.get("derivation_id")}, "qa.review_not_independent", "independent_review.reviewer_derivation_id", "Reviewer cannot reuse CAD or model derivation.")
        cad_path = resolve_path(self.project, cad.get("path"))
        model_path = resolve_path(self.project, model.get("path"))
        self.require(cad_path is not None and cad_path.is_file(), "qa.cad_evidence", "cad_provenance.path", "Independent CAD evidence is missing.")
        self.require(model_path is not None and model_path.is_file(), "qa.model_evidence", "model_provenance.path", "Active-model evidence is missing.")
        if cad_path is not None and model_path is not None and cad_path.exists() and model_path.exists():
            self.require(cad_path.resolve() != model_path.resolve(), "qa.self_referential", "provenance", "CAD and model evidence cannot be the same file.")
        for name, record in (("cad", cad), ("model", model), ("machine", self.data.get("machine_result") or {}), ("review", self.data.get("independent_review") or {})):
            self._require_hashed_file(record.get("path"), record.get("sha256"), f"qa.{name}_hash", f"{name}.sha256")

        required_views = set(self.data.get("required_views") or [])
        view_results = self.data.get("view_results") or []
        result_views = _unique_ids(view_results, "view_results", self, key="source_view_id")
        self.require(bool(required_views), "qa.required_views", "required_views", "QA view scope is empty.")
        self.require(required_views == result_views, "qa.view_coverage", "view_results", f"QA scope mismatch: missing={sorted(required_views - result_views)} extra={sorted(result_views - required_views)}")
        self.require(sum(1 for item in view_results if item.get("role") == "elevation") >= 4, "qa.four_facades", "view_results", "Full delivery requires four independently checked elevation views.")
        tolerance = float(self.data.get("tolerance_mm") or 0)
        for index, result in enumerate(view_results):
            location = f"view_results[{index}]"
            self.require(result.get("status") == "PASS", "qa.view", f"{location}.status", "Same-view comparison failed.")
            for name in ("cad_view", "model_view", "overlay"):
                artifact = result.get(name) or {}
                self._require_hashed_file(artifact.get("path"), artifact.get("sha256"), f"qa.{name}_hash", f"{location}.{name}")
            metrics = result.get("metrics") or {}
            self.require(metrics.get("false_negative_count") == 0, "qa.omission", f"{location}.metrics.false_negative_count", "CAD elements are missing from the model.")
            self.require(metrics.get("false_positive_count") == 0, "qa.extra", f"{location}.metrics.false_positive_count", "Model contains unsupported extra elements.")
            self.require(isinstance(metrics.get("max_alignment_delta_mm"), (int, float)) and float(metrics["max_alignment_delta_mm"]) <= tolerance, "qa.alignment", f"{location}.metrics.max_alignment_delta_mm", "Projected model exceeds the contracted alignment tolerance.")
            for key in ("full_view_unclipped", "same_orientation", "same_scale", "silhouette", "opening_outlines", "true_openings", "levels", "materials", "no_unsupported_geometry"):
                self.require((result.get("checks") or {}).get(key) is True, f"qa.{key}", f"{location}.checks.{key}", "Independent same-view check failed.")
        checks = self.data.get("cross_view_checks") or {}
        for key in ("every_floor_plan", "four_facades", "required_sections", "plan_elevation_registration", "plan_section_registration", "opposite_facade_orientation", "corner_continuity", "vertical_datum_consistency", "component_reuse", "canopy_assembly", "parapet_roof_silhouette", "material_junction_continuity", "untracked_geometry_clear"):
            self.require(checks.get(key) is True, f"qa.{key}", f"cross_view_checks.{key}", "Cross-view consistency check failed.")
        confirmation = self.data.get("user_confirmation") or {}
        if confirmation.get("confirmed") is True:
            self.require(confirmation.get("acceptance_basis_sha256") == qa_acceptance_basis_sha256(self.data), "qa.acceptance_basis", "user_confirmation.acceptance_basis_sha256", "Final acceptance is not bound to the current QA evidence.")
            self.require(confirmation.get("machine_result_sha256") == (self.data.get("machine_result") or {}).get("sha256"), "qa.acceptance_machine", "user_confirmation.machine_result_sha256", "Final acceptance is not bound to the current machine result.")
            self.require(confirmation.get("independent_review_sha256") == (self.data.get("independent_review") or {}).get("sha256"), "qa.acceptance_review", "user_confirmation.independent_review_sha256", "Final acceptance is not bound to the current independent review.")
            build_ref = next((item for item in self.data.get("upstream") or [] if item.get("kind") == "sketchup-build"), None)
            build_path = resolve_path(self.project, (build_ref or {}).get("path"))
            active_hash = None
            if build_path is not None and build_path.is_file():
                try:
                    active_hash = (load_json(build_path).get("active_model") or {}).get("sha256")
                except (OSError, ValueError, TypeError):
                    active_hash = None
            self.require(bool(active_hash) and confirmation.get("active_model_sha256") == active_hash, "qa.acceptance_model", "user_confirmation.active_model_sha256", "Final acceptance is not bound to the current active SKP.")
        self.require(not (self.data.get("unresolved") or []), "qa.unresolved", "unresolved", "Unresolved discrepancies block delivery.")

    def _require_file(self, value: Any, code: str, location: str) -> None:
        path = resolve_path(self.project, value)
        self.require(path is not None and path.is_file(), code, location, "Required evidence file is missing.")

    def _require_hashed_file(self, value: Any, expected: Any, code: str, location: str) -> None:
        path = resolve_path(self.project, value)
        self.require(isinstance(expected, str) and len(expected) == 64, code, location, "A full evidence SHA-256 is required.")
        if path is not None and path.is_file() and isinstance(expected, str) and len(expected) == 64:
            self.require(sha256_file(path).lower() == expected.lower(), code, location, "Evidence fingerprint is stale or invalid.")


def _unique_ids(items: Iterable[dict[str, Any]], location: str, validator: Validator, key: str = "id") -> set[str]:
    seen: set[str] = set()
    for index, item in enumerate(items):
        value = item.get(key)
        validator.require(bool(value), "contract.missing_id", f"{location}[{index}].{key}", "Stable ID is required.")
        if value:
            validator.require(str(value) not in seen, "contract.duplicate_id", f"{location}[{index}].{key}", "Stable ID is duplicated.")
            seen.add(str(value))
    return seen


def _valid_bounds(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        return float(value["xmax"]) > float(value["xmin"]) and float(value["ymax"]) > float(value["ymin"])
    except (KeyError, TypeError, ValueError):
        return False


def _valid_point(value: Any) -> bool:
    try:
        return isinstance(value, (list, tuple)) and len(value) >= 2 and all(isinstance(float(item), float) for item in value[:2])
    except (TypeError, ValueError):
        return False


def _valid_point3(value: Any) -> bool:
    try:
        return isinstance(value, (list, tuple)) and len(value) == 3 and all(isinstance(float(item), float) for item in value)
    except (TypeError, ValueError):
        return False


def _valid_nonzero_vector3(value: Any) -> bool:
    if not _valid_point3(value):
        return False
    return sum(float(item) ** 2 for item in value) > 1e-12


def _same_point(first: Any, second: Any, tolerance: float = 1e-6) -> bool:
    if not _valid_point(first) or not _valid_point(second):
        return False
    return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(first[:2], second[:2]))


def _point_on_segment(point: Any, start: Any, end: Any, tolerance: float = 1e-5) -> bool:
    if not _valid_point(point) or not _valid_point(start) or not _valid_point(end):
        return False
    px, py = float(point[0]), float(point[1])
    ax, ay = float(start[0]), float(start[1])
    bx, by = float(end[0]), float(end[1])
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5
    if length <= tolerance:
        return False
    cross = abs(dx * (py - ay) - dy * (px - ax)) / length
    dot = (px - ax) * dx + (py - ay) * dy
    return cross <= tolerance and -tolerance <= dot <= dx * dx + dy * dy + tolerance


def _valid_polygon(points: Any) -> bool:
    if not isinstance(points, list) or len(points) < 3:
        return False
    try:
        normalized = [(float(point[0]), float(point[1])) for point in points]
    except (TypeError, ValueError, IndexError):
        return False
    if len(set(normalized)) < 3 or abs(_polygon_area(normalized)) < 1e-9:
        return False
    edges = list(zip(normalized, normalized[1:] + normalized[:1]))
    for i, first in enumerate(edges):
        for j, second in enumerate(edges):
            if abs(i - j) <= 1 or {i, j} == {0, len(edges) - 1}:
                continue
            if _segments_intersect(first[0], first[1], second[0], second[1]):
                return False
    return True


def _polygon_area(points: list[tuple[float, float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1])) / 2.0


def _point_on_exterior_inner_face(point: Any, wall: dict[str, Any], footprint: list[list[float]], tolerance: float = 0.2) -> bool:
    if not _valid_point(point) or not _valid_point(wall.get("start")) or not _valid_point(wall.get("end")) or not _valid_polygon(footprint):
        return False
    px, py = float(point[0]), float(point[1])
    ax, ay = map(float, wall["start"])
    bx, by = map(float, wall["end"])
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 1e-9:
        return False
    orientation = 1.0 if _polygon_area([(float(p[0]), float(p[1])) for p in footprint]) >= 0 else -1.0
    nx, ny = orientation * -dy / length, orientation * dx / length
    thickness = float(wall.get("thickness_mm", 0))
    ix, iy = ax + nx * thickness, ay + ny * thickness
    line_distance = abs((px - ix) * dy - (py - iy) * dx) / length
    station = (px - ix) * dx / length + (py - iy) * dy / length
    return line_distance <= tolerance and -tolerance <= station <= length + tolerance


def _point_segment_distance(point: Any, start: Any, end: Any) -> float:
    if not _valid_point(point) or not _valid_point(start) or not _valid_point(end):
        return float("inf")
    px, py = float(point[0]), float(point[1])
    ax, ay = float(start[0]), float(start[1])
    bx, by = float(end[0]), float(end[1])
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 1e-12:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    ratio = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    x, y = ax + ratio * dx, ay + ratio * dy
    return ((px - x) ** 2 + (py - y) ** 2) ** 0.5


def _segments_intersect(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    def orient(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0


def _point_in_or_on_polygon(point: Any, polygon: list[list[float]]) -> bool:
    try:
        x, y = float(point[0]), float(point[1])
        pts = [(float(item[0]), float(item[1])) for item in polygon]
    except (TypeError, ValueError, IndexError):
        return False
    inside = False
    for a, b in zip(pts, pts[1:] + pts[:1]):
        cross = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
        if abs(cross) < 1e-9 and min(a[0], b[0]) - 1e-9 <= x <= max(a[0], b[0]) + 1e-9 and min(a[1], b[1]) - 1e-9 <= y <= max(a[1], b[1]) + 1e-9:
            return True
        if (a[1] > y) != (b[1] > y):
            x_at_y = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
            if x < x_at_y:
                inside = not inside
    return inside


def _polygon_contained(inner: list[list[float]], outer: list[list[float]]) -> bool:
    if not inner or not outer or not all(_point_in_or_on_polygon(point, outer) for point in inner):
        return False
    try:
        inner_pts = [(float(point[0]), float(point[1])) for point in inner]
        outer_pts = [(float(point[0]), float(point[1])) for point in outer]
    except (TypeError, ValueError, IndexError):
        return False
    for a, b in zip(inner_pts, inner_pts[1:] + inner_pts[:1]):
        for c, d in zip(outer_pts, outer_pts[1:] + outer_pts[:1]):
            if _segments_intersect(a, b, c, d):
                return False
    return True


def validate_contract(project: Path, kind: str, data: dict[str, Any]) -> list[Issue]:
    issues = validate_json_schema(kind, data)
    issues.extend(Validator(project, kind, data).validate())
    return issues


def validate_json_schema(kind: str, data: dict[str, Any]) -> list[Issue]:
    filename = SCHEMA_FILES.get(kind)
    if not filename:
        return [Issue("contract.kind", "kind", "Unknown contract kind.")]
    schema_path = Path(__file__).resolve().parents[1] / "contracts" / filename
    schema = load_json(schema_path)
    resolver = RefResolver(base_uri=schema_path.as_uri(), referrer=schema)
    validator = Draft202012Validator(schema, resolver=resolver)
    issues: list[Issue] = []
    for error in sorted(validator.iter_errors(data), key=lambda item: list(item.absolute_path)):
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        issues.append(Issue(f"schema.{error.validator}", location, error.message))
    return issues


def validate_schema_file(filename: str, data: dict[str, Any]) -> list[Issue]:
    schema_path = Path(__file__).resolve().parents[1] / "contracts" / filename
    schema = load_json(schema_path)
    resolver = RefResolver(base_uri=schema_path.as_uri(), referrer=schema)
    validator = Draft202012Validator(schema, resolver=resolver)
    issues: list[Issue] = []
    for error in sorted(validator.iter_errors(data), key=lambda item: list(item.absolute_path)):
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        issues.append(Issue(f"schema.{error.validator}", location, error.message))
    return issues
