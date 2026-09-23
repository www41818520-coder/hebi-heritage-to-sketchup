#!/usr/bin/env python3
"""Revoke the earliest failed workflow artifact and every downstream reference."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contract_validation import load_json, validate_schema_file
from independent_qa_common import ensure_inside, relative, resolve, sha256_file


SCHEMA = "cad_to_sketchup.workflow_invalidation.2026-08-06"
STAGE_KEYS = {
    "source-index": ("contracts.source-index", "topology_plan", "contracts.building-topology", "production_plan", "contracts.sketchup-build", "qa_plan", "contracts.independent-qa", "delivery_manifest", "delivery_receipt"),
    "building-topology": ("contracts.building-topology", "production_plan", "contracts.sketchup-build", "qa_plan", "contracts.independent-qa", "delivery_manifest", "delivery_receipt"),
    "sketchup-build": ("contracts.sketchup-build", "qa_plan", "contracts.independent-qa", "delivery_manifest", "delivery_receipt"),
    "independent-qa": ("qa_plan", "contracts.independent-qa", "delivery_manifest", "delivery_receipt"),
    "delivery-manifest": ("delivery_manifest", "delivery_receipt"),
}
RESTART = {"source-index": "reading", "building-topology": "topology", "sketchup-build": "build", "independent-qa": "qa", "delivery-manifest": "delivery"}


def _pop(state: dict[str, Any], key: str) -> Any:
    if key.startswith("contracts."):
        return (state.get("contracts") or {}).pop(key.split(".", 1)[1], None)
    return state.pop(key, None)


def invalidate(project: Path, state: dict[str, Any], state_before: dict[str, str], earliest: str, reason: str, reported_by: str, invalidation_id: str, created_at: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if earliest not in STAGE_KEYS: raise ValueError(f"Unsupported earliest stage: {earliest}")
    if not reason.strip(): raise ValueError("A concrete mismatch or invalidation reason is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", invalidation_id): raise ValueError("Invalidation ID contains unsupported characters")
    affected = []
    for key in STAGE_KEYS[earliest]:
        value = _pop(state, key)
        if isinstance(value, dict) and value: affected.append({"key": key, "value": value})
    if not affected: raise ValueError("No active workflow entries exist at or after the selected earliest stage")
    project_id = project.name
    for entry in affected:
        path = resolve(project, (entry["value"] or {}).get("path"))
        if path.is_file():
            try:
                project_id = str(load_json(path).get("project_id") or project_id); break
            except (OSError, ValueError, TypeError): pass
    record = {
        "schema": SCHEMA, "invalidation_id": invalidation_id, "project_id": project_id, "created_at": created_at,
        "status": "rework_required", "earliest_stage": earliest, "reason": reason.strip(), "reported_by": reported_by,
        "state_before": state_before,
        "affected_entries": affected, "required_restart_stage": RESTART[earliest]
    }
    issues = validate_schema_file("workflow-invalidation.schema.json", record)
    if issues: raise ValueError("Invalidation record is invalid: " + "; ".join(f"{x.code}@{x.location}" for x in issues))
    state["status"] = f"rework_{RESTART[earliest]}"
    state["last_invalidation"] = {"id": invalidation_id, "earliest_stage": earliest, "reason": reason.strip()}
    return record, state


def main() -> int:
    parser = argparse.ArgumentParser(description="Invalidate the earliest failed CAD-to-SketchUp stage and downstream state.")
    parser.add_argument("--project", required=True, type=Path); parser.add_argument("--state", default="work/workflow-state.json", type=Path)
    parser.add_argument("--earliest", required=True, choices=tuple(STAGE_KEYS)); parser.add_argument("--reason", required=True); parser.add_argument("--reported-by", required=True, choices=("user", "independent_qa", "system"))
    parser.add_argument("--invalidation-id"); parser.add_argument("--created-at", default=datetime.now(timezone.utc).isoformat()); parser.add_argument("--out", type=Path)
    args = parser.parse_args(); project = args.project.resolve(); state_path = resolve(project, args.state)
    stamp = re.sub(r"[^0-9]", "", args.created_at)[:14]; invalidation_id = args.invalidation_id or f"invalidate-{args.earliest}-{stamp}"
    output = ensure_inside(project, args.out or Path(f"work/invalidation/{invalidation_id}.json"), "invalidation record")
    try:
        if not state_path.is_file(): raise ValueError(f"Workflow state is missing: {state_path}")
        if output.exists(): raise ValueError(f"Refusing to overwrite invalidation record: {output}")
        snapshot = output.with_name(f"{output.stem}-state-before.json")
        if snapshot.exists(): raise ValueError(f"Refusing to overwrite workflow snapshot: {snapshot}")
        original = load_json(state_path)
        snapshot_text = json.dumps(original, ensure_ascii=False, indent=2) + "\n"
        snapshot_artifact = {"path": relative(project, snapshot), "sha256": hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()}
        record, state = invalidate(project, copy.deepcopy(original), snapshot_artifact, args.earliest, args.reason, args.reported_by, invalidation_id, args.created_at)
        snapshot.parent.mkdir(parents=True, exist_ok=True); snapshot.write_text(snapshot_text, encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state.setdefault("invalidation_history", []).append({"path": relative(project, output), "sha256": sha256_file(output)})
        state["last_invalidation"].update({"path": relative(project, output), "sha256": sha256_file(output)})
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc: print(str(exc)); return 1
    print(json.dumps({"status": "rework_required", "restart": record["required_restart_stage"], "record": relative(project, output)}, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
