from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "audit_building_topology",
    SKILL_ROOT / "scripts" / "audit_building_topology.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def candidate() -> dict:
    return {
        "levels": [{"id": "L1"}],
        "walls": [{"id": "W1"}],
        "internal_walls": [{"id": "IW1"}],
        "slabs": [{"id": "S1"}],
        "roofs": [{"id": "R1"}],
        "roof_lights": [],
        "openings": [{"id": "O1"}],
        "curtain_walls": [],
        "sweeps": [],
        "canopies": [],
        "materials": [{"id": "M1"}],
    }


def completed_boundary(value: dict) -> dict:
    boundary = MODULE.create_confirmation_boundary(value)
    boundary["presented_to_user"] = True
    for key, item in boundary["must_review"].items():
        present = item["candidate_count"] > 0
        item["source_status"] = "present" if present else "not_applicable"
        item["white_model_status"] = "present" if present else "not_applicable"
        item["evidence_view_ids"] = ["V1"]
        item["notes"] = "independently reviewed"
    return boundary


class TopologyConfirmationBoundaryTests(unittest.TestCase):
    def test_completed_boundary_passes(self) -> None:
        value = candidate()
        self.assertEqual(MODULE.confirmation_boundary_errors(value, completed_boundary(value)), [])

    def test_bare_confirmation_is_blocked(self) -> None:
        value = candidate()
        boundary = MODULE.create_confirmation_boundary(value)
        errors = MODULE.confirmation_boundary_errors(value, boundary)
        self.assertTrue(any("not presented" in error for error in errors))
        self.assertTrue(any("source status is unresolved" in error for error in errors))

    def test_source_present_but_zero_canopies_is_blocked(self) -> None:
        value = candidate()
        boundary = completed_boundary(value)
        item = boundary["must_review"]["canopies"]
        item["source_status"] = "present"
        item["white_model_status"] = "not_applicable"
        boundary["cad_detected_but_missing"] = ["canopies"]
        errors = MODULE.confirmation_boundary_errors(value, boundary)
        self.assertTrue(any("CAD-detected topology is missing" in error for error in errors))

    def test_changed_ignore_boundary_is_blocked(self) -> None:
        value = candidate()
        boundary = completed_boundary(value)
        boundary["allowed_to_ignore"] = boundary["allowed_to_ignore"][:-1]
        errors = MODULE.confirmation_boundary_errors(value, boundary)
        self.assertTrue(any("allowed-to-ignore" in error for error in errors))

    def test_stale_direct_count_is_blocked(self) -> None:
        value = candidate()
        boundary = completed_boundary(value)
        boundary["must_review"]["openings"]["candidate_count"] = 99
        errors = MODULE.confirmation_boundary_errors(value, boundary)
        self.assertTrue(any("openings candidate count is stale" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
