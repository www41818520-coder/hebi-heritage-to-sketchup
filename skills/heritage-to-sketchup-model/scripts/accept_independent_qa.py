#!/usr/bin/env python3
"""Bind explicit final user acceptance to the current independent QA evidence."""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contract_validation import load_json, qa_acceptance_basis_sha256, validate_contract
from independent_qa_common import ensure_inside, relative, resolve, sha256_file


def _build_active_hash(project: Path, qa: dict[str, Any]) -> str:
    reference = next((item for item in qa.get("upstream") or [] if item.get("kind") == "sketchup-build"), None)
    if not reference:
        raise ValueError("Independent QA has no SketchUp build upstream")
    path = resolve(project, reference.get("path"))
    if not path.is_file() or sha256_file(path).lower() != str(reference.get("sha256") or "").lower():
        raise ValueError("SketchUp build upstream is missing or stale")
    active = (load_json(path).get("active_model") or {})
    model = resolve(project, active.get("path"))
    if not model.is_file() or sha256_file(model).lower() != str(active.get("sha256") or "").lower():
        raise ValueError("Active production SKP is missing or stale")
    return sha256_file(model)


def accept(project: Path, qa: dict[str, Any], confirmation_id: str, instruction: str, accepted_at: str) -> dict[str, Any]:
    issues = validate_contract(project, "independent-qa", qa)
    if issues:
        raise ValueError("Independent QA is invalid:\n- " + "\n- ".join(f"{x.code}@{x.location}: {x.message}" for x in issues))
    if (qa.get("user_confirmation") or {}).get("confirmed") is True:
        raise ValueError("Independent QA is already accepted; do not replace an existing acceptance record")
    if not confirmation_id.strip() or not instruction.strip():
        raise ValueError("Confirmation ID and the user's explicit acceptance instruction are required")
    try:
        parsed = datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError("accepted_at must be an ISO-8601 timestamp with timezone") from exc
    result = copy.deepcopy(qa)
    result["user_confirmation"] = {
        "confirmed": True,
        "confirmation_id": confirmation_id.strip(),
        "instruction": instruction.strip(),
        "accepted_at": accepted_at,
        "acceptance_basis_sha256": qa_acceptance_basis_sha256(qa),
        "active_model_sha256": _build_active_hash(project, qa),
        "machine_result_sha256": qa["machine_result"]["sha256"],
        "independent_review_sha256": qa["independent_review"]["sha256"],
    }
    issues = validate_contract(project, "independent-qa", result)
    if issues:
        raise ValueError("Accepted QA contract is invalid:\n- " + "\n- ".join(f"{x.code}@{x.location}: {x.message}" for x in issues))
    return result


def update_state(project: Path, state_path: Path, qa_path: Path) -> None:
    if not state_path.is_file():
        return
    state = load_json(state_path)
    state.setdefault("contracts", {})["independent-qa"] = {
        "path": relative(project, qa_path), "sha256": sha256_file(qa_path)
    }
    state["status"] = "ready_for_packaging"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind final user acceptance to independent QA.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--qa", required=True, type=Path)
    parser.add_argument("--confirmation-id", required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--accepted-at", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--state", default="work/workflow-state.json", type=Path)
    args = parser.parse_args(); project = args.project.resolve()
    qa_path = resolve(project, args.qa); output = ensure_inside(project, args.out, "accepted QA contract")
    try:
        result = accept(project, load_json(qa_path), args.confirmation_id, args.instruction, args.accepted_at)
        if output.exists():
            raise ValueError(f"Refusing to overwrite accepted QA contract: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        update_state(project, resolve(project, args.state), output)
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc)); return 1
    print(json.dumps({"status": "accepted", "qa": relative(project, output), "sha256": sha256_file(output)}, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
