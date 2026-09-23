#!/usr/bin/env python3
"""Run the final delivery gate and issue an immutable DELIVERED receipt."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contract_validation import load_json
from delivery_validation import validate_delivery_manifest, validate_delivery_receipt
from independent_qa_common import ensure_inside, relative, resolve, sha256_file
from workflow_gate import validate


SCHEMA = "cad_to_sketchup.delivery_receipt.2026-08-06"


def _artifact(project: Path, path: Path) -> dict[str, str]: return {"path": relative(project, path), "sha256": sha256_file(path)}


def finalize(project: Path, state: dict[str, Any], manifest_path: Path, manifest: dict[str, Any], receipt_id: str, delivered_at: str) -> dict[str, Any]:
    errors = validate_delivery_manifest(project, manifest)
    if errors: raise ValueError("Delivery manifest is invalid:\n- " + "\n- ".join(errors))
    gate = validate(project, state, "delivery")
    if not gate.passed: raise ValueError("Delivery gate is blocked:\n- " + "\n- ".join(f"{item['code']}@{item['location']}: {item['message']}" for item in gate.blockers))
    state_manifest = state.get("delivery_manifest") or {}
    if resolve(project, state_manifest.get("path")) != manifest_path.resolve() or str(state_manifest.get("sha256") or "").lower() != sha256_file(manifest_path).lower(): raise ValueError("Workflow state points to a different delivery manifest")
    qa_ref = next(item for item in manifest["source_contracts"] if item["kind"] == "independent-qa")
    receipt = {
        "schema": SCHEMA, "receipt_id": receipt_id, "package_id": manifest["package_id"], "project_id": manifest["project_id"],
        "status": "DELIVERED", "delivered_at": delivered_at, "delivery_manifest": _artifact(project, manifest_path),
        "accepted_qa": {"path": qa_ref["path"], "sha256": qa_ref["sha256"]}, "delivered_model": manifest["delivered_model"],
        "report": manifest["report"], "confirmation_id": manifest["acceptance"]["confirmation_id"],
        "gate": {"stage": "delivery", "status": "PASS", "manifest_sha256": sha256_file(manifest_path), "qa_sha256": qa_ref["sha256"]}, "unresolved": []
    }
    issues = validate_delivery_receipt(project, receipt)
    if issues: raise ValueError("Delivery receipt is invalid: " + "; ".join(issues))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize a validated package as DELIVERED.")
    parser.add_argument("--project", required=True, type=Path); parser.add_argument("--state", default="work/workflow-state.json", type=Path); parser.add_argument("--manifest", default="work/contracts/delivery-manifest.json", type=Path)
    parser.add_argument("--receipt-id"); parser.add_argument("--delivered-at", default=datetime.now(timezone.utc).isoformat()); parser.add_argument("--out", type=Path)
    args = parser.parse_args(); project = args.project.resolve(); state_path = resolve(project, args.state); manifest_path = resolve(project, args.manifest); manifest = load_json(manifest_path)
    stamp = re.sub(r"[^0-9]", "", args.delivered_at)[:14]; receipt_id = args.receipt_id or f"receipt-{manifest.get('package_id', 'package')}-{stamp}"
    output = ensure_inside(project, args.out or Path(f"reports/delivery/{receipt_id}.json"), "delivery receipt")
    try:
        if output.exists(): raise ValueError(f"Refusing to overwrite delivery receipt: {output}")
        receipt = finalize(project, load_json(state_path), manifest_path, manifest, receipt_id, args.delivered_at)
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state = load_json(state_path); state["delivery_receipt"] = _artifact(project, output); state["status"] = "delivered"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError, StopIteration) as exc: print(str(exc)); return 1
    print(json.dumps({"status": "DELIVERED", "receipt": relative(project, output), "sha256": sha256_file(output)}, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
