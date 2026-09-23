#!/usr/bin/env python3
"""Record a correction only when it is tied to invalidation and a passing regression."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contract_validation import load_json, validate_schema_file
from independent_qa_common import ensure_inside, relative, resolve, sha256_file


SCHEMA = "cad_to_sketchup.skill_mark.2026-08-06"


def _artifact(project: Path, path: Path) -> dict[str, str]:
    return {"path": relative(project, path), "sha256": sha256_file(path)}


def create_record(project: Path, invalidation_path: Path, issue_id: str, created_at: str, mistake: str, root_cause: str, rule: str, prevention: str, rule_scope: str, promoted_to: list[str], test_file: Path, test_id: str, result_path: Path) -> dict[str, Any]:
    invalidation = load_json(invalidation_path)
    issues = validate_schema_file("workflow-invalidation.schema.json", invalidation)
    if issues: raise ValueError("Invalidation record is invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", issue_id): raise ValueError("Issue ID contains unsupported characters")
    for label, value in (("mistake", mistake), ("root cause", root_cause), ("transferable rule", rule), ("prevention check", prevention), ("test ID", test_id)):
        if not value.strip(): raise ValueError(f"{label} is required")
    if not test_file.is_file() or test_id not in test_file.read_text(encoding="utf-8-sig"): raise ValueError("Regression test file is missing or does not contain the declared test ID")
    result = load_json(result_path)
    result_tests = set(str(item) for item in result.get("tests") or [])
    if result.get("status") != "PASS" or (result.get("test_id") != test_id and test_id not in result_tests): raise ValueError("Regression result must be PASS and explicitly include the declared test ID")
    if rule_scope == "transferable" and not promoted_to: raise ValueError("A transferable rule must identify at least one skill or validation file where it was promoted")
    for value in promoted_to:
        if not resolve(project, value).is_file(): raise ValueError(f"Promoted rule target is missing: {value}")
    record = {
        "schema": SCHEMA, "issue_id": issue_id, "project_id": invalidation["project_id"], "created_at": created_at,
        "invalidation": _artifact(project, invalidation_path), "mistake": mistake.strip(), "root_cause": root_cause.strip(),
        "transferable_rule": rule.strip(), "prevention_check": prevention.strip(), "rule_scope": rule_scope,
        "promoted_to": promoted_to, "regression": {"test_file": relative(project, test_file), "test_id": test_id, "result": _artifact(project, result_path), "status": "PASS"}
    }
    issues = validate_schema_file("skill-mark.schema.json", record)
    if issues: raise ValueError("Skill mark is invalid: " + "; ".join(f"{x.code}@{x.location}" for x in issues))
    return record


def render_index(records: list[dict[str, Any]]) -> str:
    lines = ["# Skill Marks", "", "Only invalidation-bound corrections with passing regression evidence appear here.", ""]
    for item in sorted(records, key=lambda value: (value["created_at"], value["issue_id"])):
        lines += [f"## {item['issue_id']}", "", f"- Created: {item['created_at']}", f"- Mistake: {item['mistake']}", f"- Root cause: {item['root_cause']}", f"- Rule: {item['transferable_rule']}", f"- Prevention: {item['prevention_check']}", f"- Regression: `{item['regression']['test_file']}::{item['regression']['test_id']}` PASS", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a verified CAD-to-SketchUp skill correction.")
    parser.add_argument("--project", required=True, type=Path); parser.add_argument("--invalidation", required=True, type=Path); parser.add_argument("--issue-id", required=True)
    parser.add_argument("--mistake", required=True); parser.add_argument("--root-cause", required=True); parser.add_argument("--rule", required=True); parser.add_argument("--prevention-check", required=True)
    parser.add_argument("--rule-scope", choices=("transferable", "project_specific"), required=True); parser.add_argument("--promoted-to", action="append", default=[])
    parser.add_argument("--test-file", required=True, type=Path); parser.add_argument("--test-id", required=True); parser.add_argument("--test-result", required=True, type=Path)
    parser.add_argument("--created-at", default=datetime.now(timezone.utc).isoformat()); parser.add_argument("--out-dir", default="requirements/skill-marks", type=Path); parser.add_argument("--index", default="requirements/skill-marks.md", type=Path)
    args = parser.parse_args(); project = args.project.resolve(); out_dir = ensure_inside(project, args.out_dir, "skill-mark directory"); output = out_dir / f"{args.issue_id}.json"; index = ensure_inside(project, args.index, "skill-mark index")
    try:
        if output.exists(): raise ValueError(f"Refusing to overwrite skill mark: {output}")
        record = create_record(project, resolve(project, args.invalidation), args.issue_id, args.created_at, args.mistake, args.root_cause, args.rule, args.prevention_check, args.rule_scope, args.promoted_to, resolve(project, args.test_file), args.test_id, resolve(project, args.test_result))
        out_dir.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        records = [load_json(path) for path in out_dir.glob("*.json")]
        index.parent.mkdir(parents=True, exist_ok=True); index.write_text(render_index(records), encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc: print(str(exc)); return 1
    print(json.dumps({"status": "recorded", "skill_mark": relative(project, output), "regression": "PASS"}, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
