#!/usr/bin/env python3
"""Generate a CAD/model projection alignment report.

Input JSON contract:
{
  "tolerance_mm": 1.0,
  "checks": [
    {
      "id": "south-window-01",
      "view": "south_elevation",
      "cad_bounds": {"xmin": 0, "ymin": 0, "xmax": 100, "ymax": 120},
      "model_bounds": {"xmin": 0.5, "ymin": 0, "xmax": 100.5, "ymax": 120}
    }
  ]
}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


BOUND_KEYS = ("xmin", "ymin", "xmax", "ymax")
CAD_PROVENANCE_KINDS = {"dxf_extract", "cad_export", "confirmed_drawing_measurement"}
MODEL_PROVENANCE_KINDS = {"active_skp_export", "active_skp_measurement"}


def load_case(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_bounds(name: str, bounds: dict) -> None:
    missing = [key for key in BOUND_KEYS if key not in bounds]
    if missing:
        raise ValueError(f"{name} missing bound keys: {', '.join(missing)}")
    if bounds["xmax"] < bounds["xmin"] or bounds["ymax"] < bounds["ymin"]:
        raise ValueError(f"{name} has invalid bound ordering")


def max_delta(cad_bounds: dict, model_bounds: dict) -> float:
    return max(abs(float(cad_bounds[key]) - float(model_bounds[key])) for key in BOUND_KEYS)


def validate_independent_provenance(case: dict, check: dict) -> tuple[dict, dict]:
    cad = check.get("cad_provenance") or case.get("cad_provenance") or {}
    model = check.get("model_provenance") or case.get("model_provenance") or {}
    if cad.get("kind") not in CAD_PROVENANCE_KINDS:
        raise ValueError(f"{check.get('id', 'check')} has invalid CAD provenance")
    if model.get("kind") not in MODEL_PROVENANCE_KINDS:
        raise ValueError(f"{check.get('id', 'check')} has invalid model provenance")
    if not cad.get("path") or not model.get("path"):
        raise ValueError(f"{check.get('id', 'check')} provenance paths are required")
    if Path(str(cad["path"])).resolve() == Path(str(model["path"])).resolve():
        raise ValueError(f"{check.get('id', 'check')} reuses one source for CAD and model bounds")
    if cad.get("derivation_id") and cad.get("derivation_id") == model.get("derivation_id"):
        raise ValueError(f"{check.get('id', 'check')} CAD and model bounds share one derivation")
    return cad, model


def evaluate(case: dict, require_independent_provenance: bool = False) -> tuple[bool, list[dict]]:
    tolerance = float(case.get("tolerance_mm", 1.0))
    checks = case.get("checks", [])
    if not checks:
        raise ValueError("alignment case must include at least one check")
    rows = []
    for check in checks:
        cad_provenance = {}
        model_provenance = {}
        if require_independent_provenance:
            cad_provenance, model_provenance = validate_independent_provenance(case, check)
        cad_bounds = check.get("cad_bounds", {})
        model_bounds = check.get("model_bounds", {})
        validate_bounds(f"{check.get('id', 'check')}.cad_bounds", cad_bounds)
        validate_bounds(f"{check.get('id', 'check')}.model_bounds", model_bounds)
        delta = max_delta(cad_bounds, model_bounds)
        rows.append(
            {
                "id": check.get("id", "unnamed"),
                "view": check.get("view", "unknown"),
                "max_delta_mm": delta,
                "status": "PASS" if delta <= tolerance else "FAIL",
                "cad_bounds": cad_bounds,
                "model_bounds": model_bounds,
                "cad_provenance": cad_provenance,
                "model_provenance": model_provenance,
            }
        )
    return all(row["status"] == "PASS" for row in rows), rows


def render_report(case: dict, rows: list[dict], passed: bool) -> str:
    tolerance = float(case.get("tolerance_mm", 1.0))
    lines = [
        "# CAD / SketchUp Alignment QA Report",
        "",
        f"- Status: {'PASS' if passed else 'FAIL'}",
        f"- Tolerance: {tolerance:.3f} mm",
        "- Scope: numeric alignment only; this PASS is not a delivery decision.",
        "- Delivery also requires independent provenance, same-view visual overlays, silhouette, opening, and semantic QA.",
        "",
        "| Check | View | Max Delta | Status |",
        "|---|---|---:|---|",
    ]
    for row in rows:
        lines.append(f"| {row['id']} | {row['view']} | {row['max_delta_mm']:.3f} mm | {row['status']} |")
    failing = [row for row in rows if row["status"] == "FAIL"]
    if failing:
        lines.extend(["", "## Blocking Mismatches", ""])
        for row in failing:
            lines.append(
                f"- `{row['id']}` in `{row['view']}` exceeds tolerance with {row['max_delta_mm']:.3f} mm delta."
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a plan/elevation alignment QA report.")
    parser.add_argument("--input", required=True, help="JSON alignment case.")
    parser.add_argument("--output", required=True, help="Markdown report path.")
    parser.add_argument("--output-json", help="Optional machine-readable result path.")
    parser.add_argument(
        "--require-independent-provenance",
        action="store_true",
        help="Reject bounds derived from the same source or build derivation.",
    )
    args = parser.parse_args()
    case = load_case(Path(args.input))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        passed, rows = evaluate(case, require_independent_provenance=args.require_independent_provenance)
    except ValueError as exc:
        blocked = {
            "schema": "cad_to_sketchup.alignment_qa.v2",
            "status": "BLOCKED",
            "error": str(exc),
            "checks": [],
        }
        output.write_text(
            "# CAD / SketchUp Alignment QA Report\n\n"
            f"- Status: BLOCKED\n- Reason: {exc}\n",
            encoding="utf-8",
        )
        if args.output_json:
            output_json = Path(args.output_json)
            output_json.parent.mkdir(parents=True, exist_ok=True)
            output_json.write_text(json.dumps(blocked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(blocked, ensure_ascii=False))
        return 2
    output.write_text(render_report(case, rows, passed), encoding="utf-8")
    result = {
        "schema": "cad_to_sketchup.alignment_qa.v2",
        "status": "PASS" if passed else "FAIL",
        "tolerance_mm": float(case.get("tolerance_mm", 1.0)),
        "independent_provenance_required": args.require_independent_provenance,
        "checks": rows,
    }
    if args.output_json:
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS" if passed else "FAIL", "checks": len(rows)}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
