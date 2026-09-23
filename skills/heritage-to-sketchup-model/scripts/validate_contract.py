#!/usr/bin/env python3
"""CLI for validating one CAD-to-SketchUp stage contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from contract_validation import SCHEMAS, load_json, validate_contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a four-stage CAD-to-SketchUp contract.")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--kind", required=True, choices=tuple(SCHEMAS))
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    project = args.project.resolve()
    source = Path(args.input)
    if not source.is_absolute():
        source = project / source
    try:
        data = load_json(source)
        issues = validate_contract(project, args.kind, data)
        result = {
            "schema": "cad_to_sketchup.contract_report.2026-08-04",
            "kind": args.kind,
            "status": "PASS" if not issues else "BLOCKED",
            "issues": [issue.as_dict() for issue in issues],
        }
    except (OSError, ValueError, TypeError) as exc:
        result = {
            "schema": "cad_to_sketchup.contract_report.2026-08-04",
            "kind": args.kind,
            "status": "BLOCKED",
            "issues": [{"code": "contract.invalid", "location": str(source), "message": str(exc)}],
        }

    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = project / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
