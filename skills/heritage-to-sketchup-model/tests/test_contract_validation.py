import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "contract_validation.py"
SPEC = importlib.util.spec_from_file_location("contract_validation", SCRIPT)
contract_validation = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = contract_validation
SPEC.loader.exec_module(contract_validation)

GATE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "workflow_gate.py"
GATE_SPEC = importlib.util.spec_from_file_location("workflow_gate_current", GATE_SCRIPT)
workflow_gate = importlib.util.module_from_spec(GATE_SPEC)
assert GATE_SPEC.loader is not None
sys.modules[GATE_SPEC.name] = workflow_gate
GATE_SPEC.loader.exec_module(workflow_gate)

AUDIT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_building_topology.py"
AUDIT_SPEC = importlib.util.spec_from_file_location("audit_building_topology", AUDIT_SCRIPT)
audit_building_topology = importlib.util.module_from_spec(AUDIT_SPEC)
assert AUDIT_SPEC.loader is not None
sys.modules[AUDIT_SPEC.name] = audit_building_topology
AUDIT_SPEC.loader.exec_module(audit_building_topology)


class FourStageContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        for folder in ("input/cad", "work/contracts", "work/reading", "work/build", "output", "reports/qa", "reports/build"):
            (self.project / folder).mkdir(parents=True, exist_ok=True)
        self.touch("input/cad/office.dxf", b"DXF")
        frame_jpg = self.project / "work/reading/F01.jpg"
        frame_jpg.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1000, 600), "white").save(frame_jpg, "JPEG")
        self.touch("work/reading/F01.svg", b"svg")
        self.touch("work/reading/F01-manifest.json", b"{}")
        self.touch("work/reading/F01-trace.svg", b"svg")
        for view in ("P1", "RP1", "E1", "E2", "E3", "E4", "S1"):
            self.touch(f"work/reading/{view}-manifest.json", b"{}")
            self.touch(f"work/reading/{view}-trace.svg", b"svg")
        self.touch("work/topology-white-model.skp", b"topology-skp")
        for view in ("P1", "RP1", "E1", "E2", "E3", "E4", "S1"):
            self.touch(f"work/topology-review/{view}.jpg", f"topology-{view}".encode())
        topology_candidate_path = self.touch("work/contracts/topology-candidate.json", b"candidate")
        topology_review_path = self.touch("work/contracts/topology-review.json", b"review")
        self.touch("output/office.skp", b"skp")
        self.touch("reports/qa/cad.json", b"cad")
        self.touch("reports/qa/model.json", b"model")
        self.touch("reports/qa/machine-result.json", b"machine")
        self.touch("work/qa/independent-review.json", b"review")
        qa_derivation_path = self.touch("work/qa/qa-derivation.json", b"derivation")
        for view in ("plan_1f", "south", "east", "section_a"):
            self.touch(f"reports/qa/{view}.jpg", b"overlay")
        for view in ("P1", "RP1", "E1", "E2", "E3", "E4", "S1"):
            self.touch(f"reports/qa/cad-views/{view}.jpg", b"cad-view")
            self.touch(f"reports/qa/model-views/{view}.jpg", b"model-view")
            self.touch(f"reports/qa/overlays/{view}.jpg", b"overlay-view")

        self.source = self.valid_source()
        for frame in self.source["frames"]:
            frame["evidence_sha256"] = self.artifact_hashes(frame, ("preview_jpg", "vector_master", "render_manifest", "trace_bounds_svg"))
        for view in self.source["views"]:
            view["evidence_sha256"] = self.artifact_hashes(view, ("render_manifest", "trace_bounds_svg"))
        self.source_path = self.write_contract("source-index.json", self.source)
        self.topology = self.valid_topology(self.reference("source-index", self.source_path))
        self.topology["internal_walls"] = []
        self.topology["roof_lights"] = []
        self.topology["topology_audit"]["confirmation_boundary_presented"] = True
        bounds = {view["id"]: view["bounds"] for view in self.source["views"]}
        for registration in self.topology["registrations"]:
            registration["source_bounds"] = copy.deepcopy(bounds[registration["source_view_id"]])
        white_model = self.project / "work/topology-white-model.skp"
        self.topology["white_model"]["model_sha256"] = hashlib.sha256(white_model.read_bytes()).hexdigest()
        for view in self.topology["white_model"]["review_views"]:
            view["sha256"] = hashlib.sha256((self.project / view["path"]).read_bytes()).hexdigest()
        candidate_sha = hashlib.sha256(topology_candidate_path.read_bytes()).hexdigest()
        self.topology["topology_audit"]["candidate_sha256"] = candidate_sha
        self.topology["topology_audit"]["review_sha256"] = hashlib.sha256(topology_review_path.read_bytes()).hexdigest()
        self.topology["user_confirmation"]["model_sha256"] = self.topology["white_model"]["model_sha256"]
        self.topology["user_confirmation"]["candidate_sha256"] = candidate_sha
        self.topology_path = self.write_contract("building-topology.json", self.topology)
        self.production_plan = self.valid_production_plan(self.reference("building-topology", self.topology_path))
        self.production_plan_path = self.write_contract("../build/sketchup-production-plan.json", self.production_plan)
        self.touch("reports/build/sketchup-production-self-check.json", b"self-check")
        self.build = self.valid_build(self.reference("building-topology", self.topology_path))
        self.build_path = self.write_contract("sketchup-build.json", self.build)
        self.qa = self.valid_qa([
            self.reference("source-index", self.source_path),
            self.reference("building-topology", self.topology_path),
            self.reference("sketchup-build", self.build_path),
            self.reference("independent-qa-derivation", qa_derivation_path),
        ])
        self.qa_path = self.write_contract("independent-qa.json", self.qa)
        self.touch("output/deliveries/office-delivered.skp", (self.project / "output/office.skp").read_bytes())
        self.touch("reports/delivery/office-delivery.md", b"delivery-report")
        self.delivery_manifest = self.valid_delivery_manifest()
        self.delivery_manifest_path = self.write_contract("delivery-manifest.json", self.delivery_manifest)

    def tearDown(self):
        self.temp.cleanup()

    def touch(self, relative, content):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def write_contract(self, name, value):
        path = self.project / "work/contracts" / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def reference(self, kind, path):
        return {
            "kind": kind,
            "path": str(path.relative_to(self.project)).replace("\\", "/"),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def artifact_hashes(self, record, keys):
        return {
            key: hashlib.sha256((self.project / record[key]).read_bytes()).hexdigest()
            for key in keys
        }

    def valid_source(self):
        source_path = self.project / "input/cad/office.dxf"
        return {
            "schema": contract_validation.SCHEMAS["source-index"],
            "contract_id": "office-source-r1",
            "project_id": "office",
            "revision": 1,
            "status": "verified",
            "upstream": [],
            "units": {"value": "mm", "basis": "user_confirmed"},
            "reader_audit": {
                "schema": "cad_reader.independent_review.2026-08-05",
                "source_package_sha256": "a" * 64,
                "review_sha256": "b" * 64,
                "reviewer": {"id": "reader-qa", "derivation_id": "visual-review-v1"},
            },
            "semantic_summary": {
                "status": "verified",
                "qa_view_keys": ["plan_1f", "roof", "south", "east", "north", "west", "section_a"],
                "duplicate_qa_view_keys": [],
                "all_titles_canonical": True,
                "all_model_roles_resolved": True,
                "all_elevations_and_sections_directional": True,
            },
            "sources": [{
                "id": "CAD01",
                "path": "input/cad/office.dxf",
                "role": "combined_architectural_set",
                "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            }],
            "frames": [{
                "id": "F01",
                "source_id": "CAD01",
                "bounds": {"xmin": 0, "ymin": 0, "xmax": 100000, "ymax": 60000},
                "complete": True,
                "verification_state": "verified",
                "preview_jpg": "work/reading/F01.jpg",
                "vector_master": "work/reading/F01.svg",
                "render_manifest": "work/reading/F01-manifest.json",
                "trace_bounds_svg": "work/reading/F01-trace.svg",
                "render_coverage": {"status": "verified", "vector_status": "verified", "coverage_ratio": 1.0, "missing_handles": [], "missing_entity_classes": [], "edge_review": {"status": "verified", "unexplained_handles": [], "explained_handles": []}},
            }],
            "views": [
                self.view("P1", "plan", "plan_1f", 0, 0, 40000, 30000),
                self.view("RP1", "roof_plan", "roof", 500, 500, 39500, 29500),
                self.view("E1", "elevation", "south", 42000, 0, 80000, 20000),
                self.view("E2", "elevation", "east", 42000, 22000, 65000, 50000),
                self.view("E3", "elevation", "north", 66000, 22000, 85000, 50000),
                self.view("E4", "elevation", "west", 82000, 0, 99000, 20000),
                self.view("S1", "section", "section_a", 0, 32000, 40000, 58000),
            ],
            "required_roles": ["plan", "elevation", "section"],
            "unresolved": [],
        }

    @staticmethod
    def view(view_id, role, qa_view, xmin, ymin, xmax, ymax):
        title = {"plan": "首层平面图", "elevation": f"{qa_view}立面图", "section": "1-1剖面图"}.get(role, qa_view)
        direction = {
            "kind": "axis_range" if role == "elevation" else "section_line" if role == "section" else "unknown",
            "key": qa_view if role in {"elevation", "section"} else "",
            "confidence": "high" if role in {"elevation", "section"} else "low",
        }
        return {
            "id": view_id,
            "frame_id": "F01",
            "role": role,
            "title": title,
            "title_evidence": {"raw": title, "canonical": title, "confidence": "high", "fragments": [{"id": f"title-{view_id}"}]},
            "qa_view": qa_view,
            "semantic_index": {
                "title": {"canonical": title},
                "role": {"value": role, "confidence": "high"},
                "direction": direction,
                "axis": {"labels": ["1", "2"], "terminal_labels": ["1", "2"]},
                "levels": [{"value": "0.000"}] if role in {"elevation", "section"} else [],
                "dimensions": {"count": 1, "evidence_handles": [f"dimension-{view_id}"]},
                "qa_view": qa_view,
                "status": "verified",
                "blocking_reasons": [],
            },
            "bounds": {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax},
            "segmentation_method": "legacy_verified_bounds",
            "segmentation_confidence": "medium",
            "segmentation_evidence": {"reviewed": True},
            "include": True,
            "complete": True,
            "readable": True,
            "source_entity_ids": [f"entity-{view_id}"],
            "render_manifest": f"work/reading/{view_id}-manifest.json",
            "trace_bounds_svg": f"work/reading/{view_id}-trace.svg",
            "vector_coverage": {"status": "verified", "coverage_ratio": 1.0, "missing_handles": [], "missing_entity_classes": [], "edge_review": {"status": "verified", "unexplained_handles": [], "explained_handles": []}},
        }

    @staticmethod
    def valid_topology(source_reference):
        footprint = [[0, 0], [12000, 0], [12000, 8000], [0, 8000]]
        visible_ids = ["L1", "L1-W-SOUTH", "L1-W-EAST", "L1-W-NORTH", "L1-W-WEST", "SL1", "R1", "O1", "M1", "C1", "MAT1"]
        def evidence(view_id, kind):
            return {"source_view_id": view_id, "kind": kind, "source_entity_ids": [f"entity-{view_id}"]}

        registrations = []
        for view_id, qa_view, role in (
            ("P1", "plan_1f", "plan"),
            ("RP1", "roof", "roof_plan"),
            ("E1", "south", "elevation"),
            ("E2", "east", "elevation"),
            ("E3", "north", "elevation"),
            ("E4", "west", "elevation"),
            ("S1", "section_a", "section"),
        ):
            kind = "measured_plan" if role in {"plan", "roof_plan"} else "measured_elevation" if role == "elevation" else "measured_section"
            registrations.append({
                "id": f"REG-{view_id}",
                "source_view_id": view_id,
                "qa_view": qa_view,
                "role": role,
                "transform": {"source_origin": [0, 0], "origin": [0, 0, 0], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0] if role == "plan" else [0, 0, 1], "scale": 1.0},
                "visible_topology_ids": visible_ids,
                "anchor_evidence": [evidence(view_id, kind), {"source_view_id": view_id, "kind": kind, "source_entity_ids": [f"anchor-2-{view_id}"]}],
                "status": "verified",
            })

        walls = [
            {"id": "L1-W-SOUTH", "level_id": "L1", "start": [0, 0], "end": [12000, 0], "thickness_mm": 240, "owns_exterior_face": True, "evidence": [evidence("P1", "measured_plan")]},
            {"id": "L1-W-EAST", "level_id": "L1", "start": [12000, 0], "end": [12000, 8000], "thickness_mm": 240, "owns_exterior_face": True, "evidence": [evidence("P1", "measured_plan")]},
            {"id": "L1-W-NORTH", "level_id": "L1", "start": [12000, 8000], "end": [0, 8000], "thickness_mm": 240, "owns_exterior_face": True, "evidence": [evidence("P1", "measured_plan")]},
            {"id": "L1-W-WEST", "level_id": "L1", "start": [0, 8000], "end": [0, 0], "thickness_mm": 240, "owns_exterior_face": True, "evidence": [evidence("P1", "measured_plan")]},
        ]
        checks = {key: True for key in contract_validation.TOPOLOGY_AUDIT_CHECKS}
        return {
            "schema": contract_validation.SCHEMAS["building-topology"],
            "contract_id": "office-topology-r1",
            "project_id": "office",
            "revision": 1,
            "status": "confirmed",
            "upstream": [source_reference],
            "derivation": {"id": "topology-derive-v1", "agent_role": "topology_derivation"},
            "coordinate_system": {"units": "mm", "origin": [0, 0, 0], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1], "elevation_datum": "architectural_0.000"},
            "registrations": registrations,
            "control_basis": {
                "priority": ["user_confirmation", "control_mass", "cad_measurement", "documented_default"],
                "control_mass": {"provided": False, "scope": "none"},
            },
            "white_model": {
                "model_path": "work/topology-white-model.skp",
                "model_sha256": "",
                "stage": "topology_white_model",
                "review_views": [
                    {"source_view_id": view_id, "role": role, "path": f"work/topology-review/{view_id}.jpg", "sha256": ""}
                    for view_id, role in (("P1", "plan"), ("RP1", "roof_plan"), ("E1", "elevation"), ("E2", "elevation"), ("E3", "elevation"), ("E4", "elevation"), ("S1", "section"))
                ],
            },
            "topology_audit": {
                "schema": "cad_to_sketchup.topology_review.2026-08-05",
                "status": "verified",
                "candidate_path": "work/contracts/topology-candidate.json",
                "candidate_sha256": "",
                "review_path": "work/contracts/topology-review.json",
                "review_sha256": "",
                "reviewer": {"id": "topology-qa", "derivation_id": "topology-review-v1"},
                "topology_derivation_id": "topology-derive-v1",
                "checks": checks,
            },
            "user_confirmation": {"confirmed": True, "confirmation_id": "confirm-1", "instruction": "确认拓扑白模", "model_sha256": "", "candidate_sha256": ""},
            "levels": [{
                "id": "L1",
                "elevation_mm": 0,
                "top_elevation_mm": 4200,
                "footprint": footprint,
                "exterior_wall_ring": {"closed": True, "continuous_group": True, "wall_ids": ["L1-W-SOUTH", "L1-W-EAST", "L1-W-NORTH", "L1-W-WEST"]},
                "evidence": [evidence("P1", "measured_plan"), evidence("S1", "measured_section")],
            }],
            "walls": walls,
            "slabs": [{"id": "SL1", "level_id": "L1", "boundary": footprint, "top_elevation_mm": 0, "thickness_mm": 150, "evidence": [evidence("P1", "measured_plan"), evidence("S1", "measured_section")]}],
            "roofs": [{"id": "R1", "base_level_id": "L1", "boundary": footprint, "base_elevation_mm": 4200, "thickness_mm": 180, "topology_type": "flat", "evidence": [evidence("E1", "measured_elevation"), evidence("S1", "measured_section")]}],
            "openings": [{
                "id": "O1",
                "level_id": "L1",
                "host_wall_id": "L1-W-SOUTH",
                "type": "window",
                "position": [4000, 0],
                "sill_elevation_mm": 900,
                "width_mm": 1800,
                "height_mm": 2100,
                "wall_thickness_mm": 240,
                "cut_depth_mm": 240,
                "depth_basis": "measured_wall_thickness",
                "through_cut": True,
                "evidence": [evidence("P1", "measured_plan"), evidence("E1", "measured_elevation")],
            }],
            "curtain_walls": [],
            "sweeps": [{
                "id": "M1",
                "path": [[0, 0, 4000], [12000, 0, 4000], [12000, 8000, 4000]],
                "path_continuous": True,
                "profile": [[0, 0], [120, 0], [120, 180], [0, 180]],
                "profile_basis": {"u_axis": [0, -1, 0], "v_axis": [0, 0, 1]},
                "profile_evidence": evidence("E1", "measured_end_profile"),
                "corner_policy": "continuous_turn",
                "termination_policy": "evidence_bound_ends",
                "covered_facades": ["south", "east"],
                "datum_evidence": [evidence("E1", "measured_elevation"), evidence("E2", "measured_elevation")],
                "evidence": [evidence("E1", "measured_elevation"), evidence("E2", "measured_elevation")],
            }],
            "canopies": [{
                "id": "C1",
                "host_wall_id": "L1-W-SOUTH",
                "footprint": [[3000, -1800], [7000, -1800], [7000, 0], [3000, 0]],
                "base_elevation_mm": 3000,
                "thickness_mm": 200,
                "closed_volume": True,
                "orthogonal_elevation_views": ["E1", "E2"],
                "thickness_evidence": evidence("E1", "measured_elevation"),
                "evidence": [evidence("E1", "measured_elevation"), evidence("E2", "measured_elevation")],
            }],
            "materials": [{
                "id": "MAT1",
                "geometric_effect": False,
                "evidence": evidence("E1", "cad_annotation"),
            }],
            "unresolved": [],
        }

    def valid_build(self, topology_reference):
        model_path = self.project / "output/office.skp"
        ids = ["L1", "L1-W-SOUTH", "L1-W-EAST", "L1-W-NORTH", "L1-W-WEST", "SL1", "R1", "O1", "M1", "C1", "MAT1"]
        plan_path = self.project / "work/build/sketchup-production-plan.json"
        report_path = self.project / "reports/build/sketchup-production-self-check.json"
        return {
            "schema": contract_validation.SCHEMAS["sketchup-build"],
            "contract_id": "office-build-r1",
            "project_id": "office",
            "revision": 1,
            "status": "verified",
            "upstream": [topology_reference],
            "generator": {"module": "HEBIProductionBuild", "version": "2026-08-06", "root_group": "HEBI_PRODUCTION_BUILD_2026_08_06", "root_persistent_id": 42},
            "build_plan": {"path": "work/build/sketchup-production-plan.json", "sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest()},
            "execution_report": {"path": "reports/build/sketchup-production-self-check.json", "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest()},
            "active_model": {
                "path": "output/office.skp",
                "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            },
            "required_topology_ids": ids,
            "element_results": [
                {"topology_id": item, "kind": "level" if item == "L1" else "wall" if "-W-" in item else "slab" if item == "SL1" else "roof" if item == "R1" else "opening" if item == "O1" else "sweep" if item == "M1" else "canopy" if item == "C1" else "material", "status": "PASS", "sketchup_entity_ids": [index + 1], "group_name": f"PROD_{item}", "generated_solids": 0 if item == "MAT1" else 1, "failed_solids": 0}
                for index, item in enumerate(ids)
            ],
            "statistics": {"unique_semantic_inputs": len(ids), "level_processing_occurrences": 1, "generated_solids": len(ids), "failed_solids": 0, "unique_openings": 1, "opening_occurrences": 1, "output_bounds_mm": {"min": [0, -1800, -150], "max": [12000, 8000, 4380]}},
            "geometry_checks": {
                "complete_element_coverage": True,
                "continuous_wall_groups": True,
                "slabs_contained": True,
                "true_openings": True,
                "opening_assemblies_complete": True,
                "curtain_wall_systems_complete": True,
                "corner_continuity": True,
                "no_duplicate_exterior_faces": True,
                "canopies_complete": True,
                "materials_traceable": True,
                "debug_geometry_clear": True,
            },
            "unresolved": [],
        }

    def valid_production_plan(self, topology_reference):
        ids = ["L1", "L1-W-SOUTH", "L1-W-EAST", "L1-W-NORTH", "L1-W-WEST", "SL1", "R1", "O1", "M1", "C1", "MAT1"]
        evidence = self.topology["openings"][0]["evidence"]
        return {
            "schema": "cad_to_sketchup.sketchup_production_plan.2026-08-06",
            "contract_id": "office-build-r1", "project_id": "office", "revision": 1,
            "status": "ready_for_sketchup", "execution_allowed": True, "build_plan_id": "office-build-r1-plan", "project_root": str(self.project.resolve()),
            "upstream": [topology_reference], "derivation": {"id": "production-spec-v1", "agent_role": "production_specification"},
            "target": {"source_model_path": "work/topology-white-model.skp", "source_model_sha256": self.topology["white_model"]["model_sha256"], "output_model_path": "output/office.skp", "result_path": "work/build/sketchup-production-result.json", "report_path": "reports/build/sketchup-production-self-check.json"},
            "required_topology_ids": ids,
            "opening_assemblies": [{"topology_id": "O1", "status": "verified", "family_id": "WIN-A", "frame_width_mm": 60, "frame_depth_mm": 80, "inset_mm": 40, "panel_type": "glass", "mullion_ratios": [0.5], "transom_ratios": [], "evidence": evidence}],
            "curtain_wall_systems": [], "material_assignments": [{"material_id": "MAT1", "status": "verified", "display_name": "Facade white", "rgba": [230, 230, 225, 255], "target_topology_ids": ["L1"], "evidence": [self.topology["materials"][0]["evidence"]]}],
            "generator": {"module": "HEBIProductionBuild", "root_group": "HEBI_PRODUCTION_BUILD_2026_08_06", "version": "2026-08-06"}, "unresolved": [],
        }

    def valid_qa(self, upstream):
        views = (("P1", "plan_1f", "plan"), ("RP1", "roof", "roof_plan"), ("E1", "south", "elevation"), ("E2", "east", "elevation"), ("E3", "north", "elevation"), ("E4", "west", "elevation"), ("S1", "section_a", "section"))
        artifact = lambda path: {"path": path, "sha256": hashlib.sha256((self.project / path).read_bytes()).hexdigest()}
        checks = {key: True for key in ("full_view_unclipped", "same_orientation", "same_scale", "silhouette", "opening_outlines", "true_openings", "levels", "materials", "no_unsupported_geometry")}
        qa = {
            "schema": contract_validation.SCHEMAS["independent-qa"],
            "contract_id": "office-qa-r1",
            "project_id": "office",
            "revision": 1,
            "status": "PASS",
            "tolerance_mm": 1.0,
            "upstream": upstream,
            "cad_provenance": {**artifact("reports/qa/cad.json"), "derivation_id": "cad-reader-independent"},
            "model_provenance": {**artifact("reports/qa/model.json"), "derivation_id": "active-skp-export"},
            "machine_result": artifact("reports/qa/machine-result.json"),
            "independent_review": {**artifact("work/qa/independent-review.json"), "reviewer_derivation_id": "qa-review-independent"},
            "required_views": [view_id for view_id, _, _ in views],
            "view_results": [{
                "source_view_id": view_id, "qa_view": qa_view, "role": role, "status": "PASS",
                "cad_view": artifact(f"reports/qa/cad-views/{view_id}.jpg"),
                "model_view": artifact(f"reports/qa/model-views/{view_id}.jpg"),
                "overlay": artifact(f"reports/qa/overlays/{view_id}.jpg"),
                "metrics": {"false_negative_count": 0, "false_positive_count": 0, "max_alignment_delta_mm": 0.0},
                "checks": copy.deepcopy(checks),
            } for view_id, qa_view, role in views],
            "cross_view_checks": {key: True for key in ("every_floor_plan", "four_facades", "required_sections", "plan_elevation_registration", "plan_section_registration", "opposite_facade_orientation", "corner_continuity", "vertical_datum_consistency", "component_reuse", "canopy_assembly", "parapet_roof_silhouette", "material_junction_continuity", "untracked_geometry_clear")},
            "user_confirmation": {"confirmed": False},
            "unresolved": [],
        }
        qa["user_confirmation"] = {
            "confirmed": True,
            "confirmation_id": "final-acceptance-1",
            "instruction": "接受当前哈希绑定的模型与独立质检证据",
            "accepted_at": "2026-08-06T12:00:00+08:00",
            "acceptance_basis_sha256": contract_validation.qa_acceptance_basis_sha256(qa),
            "active_model_sha256": hashlib.sha256((self.project / "output/office.skp").read_bytes()).hexdigest(),
            "machine_result_sha256": qa["machine_result"]["sha256"],
            "independent_review_sha256": qa["independent_review"]["sha256"],
        }
        return qa

    def valid_delivery_manifest(self):
        artifact = lambda path: {"path": path, "sha256": hashlib.sha256((self.project / path).read_bytes()).hexdigest()}
        evidence = []
        for kind in ("cad_provenance", "model_provenance", "machine_result", "independent_review"):
            evidence.append({"kind": kind, "path": self.qa[kind]["path"], "sha256": self.qa[kind]["sha256"]})
        for view in self.qa["view_results"]:
            for kind in ("cad_view", "model_view", "overlay"):
                evidence.append({"kind": kind, "source_view_id": view["source_view_id"], "path": view[kind]["path"], "sha256": view[kind]["sha256"]})
        acceptance = copy.deepcopy(self.qa["user_confirmation"]); acceptance.pop("confirmed")
        return {
            "schema": "cad_to_sketchup.delivery_manifest.2026-08-06", "package_id": "office-delivery-1", "project_id": "office", "revision": 1,
            "status": "packaged_for_delivery_gate", "created_at": "2026-08-06T12:30:00+08:00", "project_root": str(self.project.resolve()),
            "source_contracts": [self.reference("source-index", self.source_path), self.reference("building-topology", self.topology_path), self.reference("sketchup-build", self.build_path), self.reference("independent-qa", self.qa_path)],
            "acceptance": acceptance, "active_model": copy.deepcopy(self.build["active_model"]), "delivered_model": artifact("output/deliveries/office-delivered.skp"),
            "evidence": evidence, "report": artifact("reports/delivery/office-delivery.md"), "unresolved": [],
        }

    def codes(self, kind, data):
        return {item.code for item in contract_validation.validate_contract(self.project, kind, data)}

    def test_all_four_valid_contracts_pass(self):
        self.assertFalse(self.codes("source-index", self.source))
        self.assertFalse(self.codes("building-topology", self.topology))
        self.assertFalse(self.codes("sketchup-build", self.build))
        self.assertFalse(self.codes("independent-qa", self.qa))

    def test_json_schema_rejects_undeclared_contract_fields(self):
        broken = copy.deepcopy(self.topology)
        broken["silent_guess"] = True
        self.assertIn("schema.additionalProperties", self.codes("building-topology", broken))

    def test_current_workflow_gate_requires_contract_chain(self):
        state = {
            "schema": workflow_gate.SCHEMA,
            "contracts": {
                "source-index": self.reference("source-index", self.source_path),
                "building-topology": self.reference("building-topology", self.topology_path),
                "sketchup-build": self.reference("sketchup-build", self.build_path),
                "independent-qa": self.reference("independent-qa", self.qa_path),
            },
            "production_plan": self.reference("production-plan", self.production_plan_path),
            "delivery_manifest": self.reference("delivery-manifest", self.delivery_manifest_path),
            "sketchup_target": {"confirmed": True, "model_path": "output/office.skp"},
        }
        for stage in ("reading", "build", "qa", "delivery"):
            report = workflow_gate.validate(self.project, state, stage)
            self.assertTrue(report.passed, (stage, report.blockers))

        state["contracts"]["building-topology"]["sha256"] = "0" * 64
        report = workflow_gate.validate(self.project, state, "build")
        self.assertFalse(report.passed)
        self.assertIn("contract.invalid_hash", {item["code"] for item in report.blockers})

    def test_machine_qa_can_pass_before_final_user_acceptance(self):
        qa = copy.deepcopy(self.qa)
        qa["user_confirmation"] = {"confirmed": False}
        self.assertFalse(self.codes("independent-qa", qa))
        qa_path = self.write_contract("independent-qa-awaiting-user.json", qa)
        state = {
            "schema": workflow_gate.SCHEMA,
            "contracts": {
                "source-index": self.reference("source-index", self.source_path),
                "building-topology": self.reference("building-topology", self.topology_path),
                "sketchup-build": self.reference("sketchup-build", self.build_path),
                "independent-qa": self.reference("independent-qa", qa_path),
            },
            "production_plan": self.reference("production-plan", self.production_plan_path),
            "sketchup_target": {"confirmed": True, "model_path": "output/office.skp"},
        }
        report = workflow_gate.validate(self.project, state, "delivery")
        self.assertFalse(report.passed)
        self.assertIn("delivery.user_confirmation", {item["code"] for item in report.blockers})

    def test_source_index_blocks_partial_or_unreadable_view(self):
        broken = copy.deepcopy(self.source)
        broken["views"][1]["complete"] = False
        broken["views"][1]["readable"] = False
        codes = self.codes("source-index", broken)
        self.assertIn("source.view_incomplete", codes)
        self.assertIn("source.view_unreadable", codes)

    def test_source_index_blocks_unexplained_crop_edge_geometry(self):
        broken = copy.deepcopy(self.source)
        broken["views"][1]["vector_coverage"]["edge_review"] = {
            "status": "needs_verification",
            "unexplained_handles": ["ELEV-ROOF-EDGE"],
            "explained_handles": [],
        }
        codes = self.codes("source-index", broken)
        self.assertIn("source.view_edge_review", codes)
        self.assertIn("source.view_edge_touch", codes)

    def test_source_index_blocks_missing_render_handle_and_stale_manifest(self):
        broken = copy.deepcopy(self.source)
        broken["frames"][0]["render_coverage"]["vector_status"] = "blocked"
        broken["frames"][0]["render_coverage"]["coverage_ratio"] = 0.99
        broken["frames"][0]["render_coverage"]["missing_handles"] = ["CAD-HANDLE-42"]
        broken["frames"][0]["render_coverage"]["missing_entity_classes"] = ["TEXT"]
        broken["frames"][0]["evidence_sha256"]["render_manifest"] = "0" * 64
        codes = self.codes("source-index", broken)
        self.assertIn("source.vector_coverage", codes)
        self.assertIn("source.coverage_ratio", codes)
        self.assertIn("source.missing_handles", codes)
        self.assertIn("source.coverage_missing", codes)
        self.assertIn("source.evidence_hash", codes)

    def test_office_regressions_block_at_topology_contract(self):
        broken = copy.deepcopy(self.topology)
        broken["slabs"][0]["boundary"] = [[0, 0], [12500, 0], [12500, 8000], [0, 8000]]
        broken["openings"][0]["through_cut"] = False
        broken["openings"][0]["cut_depth_mm"] = 100
        broken["sweeps"][0]["corner_policy"] = "stop_at_facade_edge"
        broken["canopies"][0]["orthogonal_elevation_views"] = ["south"]
        broken["canopies"][0]["thickness_evidence"] = {"kind": "default"}
        broken["materials"][0] = {
            "id": "MAT1",
            "geometric_effect": True,
            "evidence": {"kind": "cad_color_only"},
        }
        codes = self.codes("building-topology", broken)
        self.assertIn("topology.slab_outside", codes)
        self.assertIn("topology.opening_through", codes)
        self.assertIn("topology.opening_depth", codes)
        self.assertIn("topology.sweep_corner", codes)
        self.assertIn("topology.canopy_cross_view", codes)
        self.assertIn("topology.canopy_thickness", codes)
        self.assertIn("topology.material_geometry", codes)

    def test_registration_visible_scope_cannot_reference_unknown_topology(self):
        broken = copy.deepcopy(self.topology)
        broken["registrations"][0]["visible_topology_ids"].append("INVENTED-FACADE")
        self.assertIn("topology.registration_unknown_topology", self.codes("building-topology", broken))

    def test_independent_topology_review_promotes_candidate(self):
        candidate = copy.deepcopy(self.topology)
        candidate["schema"] = audit_building_topology.CANDIDATE_SCHEMA
        candidate["status"] = "candidate"
        candidate.pop("topology_audit")
        candidate.pop("user_confirmation")
        candidate_path = self.write_contract("topology-candidate.json", candidate)
        review = audit_building_topology.create_review_template(self.project, candidate_path, candidate)
        import review_session
        review_session.configure(self.project, "human")
        template_path = self.write_contract("topology-review-template.json", review)
        packet_path = review_session.prepare(self.project, "topology", template_path,
                                             [candidate_path, self.project / candidate["white_model"]["model_path"],
                                              *[self.project / row["path"] for row in candidate["white_model"]["review_views"]]],
                                             candidate["derivation"]["id"])
        review["reviewer"] = {"id": "independent-topology-qa", "derivation_id": "topology-review-v2"}
        review["status"] = "verified"
        review["checks"] = {key: True for key in contract_validation.TOPOLOGY_AUDIT_CHECKS}
        boundary = review["confirmation_boundary"]
        boundary["presented_to_user"] = True
        for item in boundary["must_review"].values():
            present = item["candidate_count"] > 0
            item.update(source_status="present" if present else "not_applicable",
                        white_model_status="present" if present else "not_applicable",
                        evidence_view_ids=["P1", "E1", "S1"], notes="Synthetic fixture inventory checked.")
        for result in review["view_results"]:
            result["status"] = "PASS"
            result["checks"] = {key: True for key in audit_building_topology.VIEW_CHECKS}
        review["user_confirmation"].update({
            "confirmed": True,
            "confirmation_id": "user-confirm-topology-2",
            "instruction": "确认拓扑白模",
        })
        review_path = self.write_contract("topology-review.json", review)
        response_path = self.touch("work/topology-human-response.txt", b"Synthetic user reviewed all presented topology checks and accepted this candidate.")
        review_session.record(self.project, packet_path, response_path, review_path)
        review_session.require_receipt(self.project, review_path, "topology")
        promoted = audit_building_topology.promote(self.project, candidate_path, review_path)
        self.assertEqual(promoted["status"], "confirmed")
        self.assertFalse(self.codes("building-topology", promoted))

    def test_topology_review_rejects_same_derivation_and_incomplete_check(self):
        candidate = copy.deepcopy(self.topology)
        candidate["schema"] = audit_building_topology.CANDIDATE_SCHEMA
        candidate["status"] = "candidate"
        candidate.pop("topology_audit")
        candidate.pop("user_confirmation")
        candidate_path = self.write_contract("topology-candidate.json", candidate)
        review = audit_building_topology.create_review_template(self.project, candidate_path, candidate)
        review["reviewer"] = {"id": "self-review", "derivation_id": candidate["derivation"]["id"]}
        review["status"] = "verified"
        review["checks"] = {key: True for key in contract_validation.TOPOLOGY_AUDIT_CHECKS}
        review["checks"]["wall_rings_closed"] = False
        review["user_confirmation"].update({"confirmed": True, "confirmation_id": "confirm", "instruction": "确认"})
        errors = audit_building_topology.review_errors(self.project, candidate_path, candidate, review)
        self.assertTrue(any("must differ" in item for item in errors))
        self.assertTrue(any("wall_rings_closed" in item for item in errors))

    def test_invalid_topology_candidate_cannot_enter_review(self):
        candidate = copy.deepcopy(self.topology)
        candidate["schema"] = audit_building_topology.CANDIDATE_SCHEMA
        candidate["status"] = "candidate"
        candidate.pop("topology_audit")
        candidate.pop("user_confirmation")
        candidate["slabs"][0]["boundary"] = [[0, 0], [13000, 0], [13000, 8000], [0, 8000]]
        candidate_path = self.write_contract("invalid-topology-candidate.json", candidate)
        with self.assertRaisesRegex(ValueError, "topology.slab_outside"):
            audit_building_topology.create_review_template(self.project, candidate_path, candidate)

    def test_build_blocks_missing_elements_and_known_geometry_defects(self):
        broken = copy.deepcopy(self.build)
        broken["element_results"] = broken["element_results"][:-1]
        broken["geometry_checks"]["corner_continuity"] = False
        broken["geometry_checks"]["no_duplicate_exterior_faces"] = False
        codes = self.codes("sketchup-build", broken)
        self.assertIn("build.coverage", codes)
        self.assertIn("build.corner_continuity", codes)
        self.assertIn("build.no_duplicate_exterior_faces", codes)

    def test_qa_blocks_self_reference_omissions_and_extra_geometry(self):
        broken = copy.deepcopy(self.qa)
        broken["model_provenance"] = copy.deepcopy(broken["cad_provenance"])
        broken["view_results"][0]["metrics"] = {
            "false_negative_count": 2,
            "false_positive_count": 1,
        }
        codes = self.codes("independent-qa", broken)
        self.assertIn("qa.same_derivation", codes)
        self.assertIn("qa.self_referential", codes)
        self.assertIn("qa.omission", codes)
        self.assertIn("qa.extra", codes)


if __name__ == "__main__":
    unittest.main()
