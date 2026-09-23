import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import review_session as review
from audit_cad_reader_package import create_review_template


class ReviewSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.evidence = self.root / "source.txt"
        self.evidence.write_text("fixture evidence", encoding="utf-8")
        self.template = self.root / "template.json"
        self.value = create_review_template({"frames": [{"id": "F1"}], "regions": [{"id": "V1"}]}, "a" * 64)
        review.write(self.template, self.value)

    def packet(self, mode="human"):
        review.configure(self.root, mode, "available reviewer" if mode == "agent" else "")
        return review.prepare(self.root, "reading", self.template, [self.evidence], "producer")

    def completed(self, mode="human"):
        data = copy.deepcopy(self.value)
        data["reviewer"] = {"type": "human" if mode == "human" else "independent_agent", "id": "reviewer", "derivation_id": "review-1"}
        for section in ("frames", "views"):
            for checks in data[section].values():
                for key in checks:
                    checks[key] = True
        path = self.root / "completed.json"
        review.write(path, data)
        response = self.root / "response.txt"
        response.write_text("I inspected F1 and V1 against all the presented checks; accepted.", encoding="utf-8")
        return path, response

    def test_human_and_agent_paths(self):
        for mode in ("human", "agent"):
            with self.subTest(mode=mode):
                packet = self.packet(mode)
                if (self.root / "completed.json").exists():
                    # Separate fixtures for each reviewer, not mutable production history.
                    (self.root / "completed.json").unlink()
                result, response = self.completed(mode)
                receipt = review.record(self.root, packet, response, result)
                self.assertEqual(review.load(receipt)["mode"], mode)
                self.assertEqual(review.load(receipt)["status"], "recorded_not_promoted")
                review.require_receipt(self.root, result, "reading")

    def test_switch_preserves_old_packet_route(self):
        packet = self.packet()
        review.configure(self.root, "agent", "another provider")
        result, response = self.completed()
        receipt = review.record(self.root, packet, response, result)
        self.assertEqual(review.load(receipt)["mode"], "human")
        self.assertTrue(list((self.root / "work/review").glob("settings-*.json")))

    def test_no_provider_is_required_for_human(self):
        review.configure(self.root, "human")
        with self.assertRaises(ValueError):
            review.configure(self.root, "agent")

    def test_no_mode_no_packet(self):
        with self.assertRaises(OSError):
            review.prepare(self.root, "reading", self.template, [self.evidence], "producer")

    def test_no_response_no_receipt(self):
        packet = self.packet()
        result, response = self.completed()
        response.write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            review.record(self.root, packet, response, result)

    def test_stale_evidence_blocks(self):
        packet = self.packet()
        result, response = self.completed()
        self.evidence.write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            review.record(self.root, packet, response, result)

    def test_pending_or_missing_checks_block(self):
        packet = self.packet()
        result, response = self.completed()
        data = review.load(result)
        data["frames"]["F1"]["not_clipped"] = False
        result.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            review.record(self.root, packet, response, result)

    def test_producer_cannot_review(self):
        packet = self.packet()
        result, response = self.completed()
        data = review.load(result)
        data["reviewer"]["id"] = "producer"
        result.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            review.record(self.root, packet, response, result)

    def test_changed_response_invalidates_receipt(self):
        packet = self.packet()
        result, response = self.completed()
        review.record(self.root, packet, response, result)
        response.write_text("different answer", encoding="utf-8")
        with self.assertRaises(ValueError):
            review.require_receipt(self.root, result, "reading")

    def test_receipt_cannot_be_overwritten(self):
        packet = self.packet()
        result, response = self.completed()
        review.record(self.root, packet, response, result)
        with self.assertRaises(FileExistsError):
            review.record(self.root, packet, response, result)

    def test_outside_project_rejected(self):
        with self.assertRaises(ValueError):
            review.inside(self.root, Path("../outside"))

    def test_stage_mismatch_rejected(self):
        review.configure(self.root, "human")
        with self.assertRaises(ValueError):
            review.prepare(self.root, "qa", self.template, [self.evidence], "producer")

    def test_qa_both_routes_and_pending_checks(self):
        from finalize_independent_qa import REVIEW_SCHEMA, VIEW_CHECKS, CROSS_CHECKS
        for mode in ("human", "agent"):
            with self.subTest(mode=mode):
                review.configure(self.root, mode, "independent reviewer" if mode == "agent" else "")
                value = {"schema": REVIEW_SCHEMA, "status": "review_required",
                         "machine_result": review.artifact(self.root, self.evidence),
                         "reviewer": {"id": "", "derivation_id": ""},
                         "view_results": [{"source_view_id": "P1", "status": "REVIEW", "notes": "",
                                           "checks": {key: False for key in VIEW_CHECKS}}],
                         "cross_view_checks": {key: False for key in CROSS_CHECKS}, "unresolved": []}
                template = self.root / f"qa-{mode}.json"
                review.write(template, value)
                packet = review.prepare(self.root, "qa", template, [self.evidence], "producer")
                value["reviewer"] = {"id": "independent reviewer", "derivation_id": "qa-review"}
                completed = self.root / f"qa-{mode}-completed.json"
                review.write(completed, value)
                response = self.root / f"qa-{mode}-response.txt"
                response.write_text("Fixture reviewer response", encoding="utf-8")
                with self.assertRaises(ValueError):
                    review.record(self.root, packet, response, completed)
                value["status"] = "verified"
                value["view_results"][0]["status"] = "PASS"
                value["view_results"][0]["checks"] = dict.fromkeys(VIEW_CHECKS, True)
                value["cross_view_checks"] = dict.fromkeys(CROSS_CHECKS, True)
                completed.write_text(json.dumps(value), encoding="utf-8")
                review.record(self.root, packet, response, completed)
                review.require_receipt(self.root, completed, "qa")

    def test_no_receipt_blocks_stage(self):
        result, _ = self.completed()
        with self.assertRaises(ValueError):
            review.require_receipt(self.root, result, "reading")

    def test_null_topology_checks_are_presented(self):
        self.assertEqual(review.checklist({"checks": {"registration_correct": None}}),
                         ["- [ ] /checks/registration_correct"])


if __name__ == "__main__":
    unittest.main()
