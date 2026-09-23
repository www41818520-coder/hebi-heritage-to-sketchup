#!/usr/bin/env python3
"""Create a non-overwriting timestamped SKP delivery package and manifest."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contract_validation import load_json, validate_contract
from delivery_validation import validate_delivery_manifest
from independent_qa_common import ensure_inside, relative, resolve, sha256_file


SCHEMA = "cad_to_sketchup.delivery_manifest.2026-08-06"


def _artifact(project: Path, path: Path) -> dict[str, str]:
    return {"path": relative(project, path), "sha256": sha256_file(path)}


def _evidence(qa: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for kind in ("cad_provenance", "model_provenance", "machine_result", "independent_review"):
        item = qa[kind]; rows.append({"kind": kind, "path": item["path"], "sha256": item["sha256"]})
    for view in qa["view_results"]:
        for kind in ("cad_view", "model_view", "overlay"):
            item = view[kind]
            rows.append({"kind": kind, "source_view_id": view["source_view_id"], "path": item["path"], "sha256": item["sha256"]})
    return rows


def render_report(package_id: str, qa: dict[str, Any], delivered: dict[str, str], created_at: str) -> str:
    confirmation = qa["user_confirmation"]
    lines = [
        "# CAD-to-SketchUp 建模交付报告", "", "## 交付状态", "",
        "- 当前状态：待最终交付门槛验证", f"- 交付包：`{package_id}`", f"- 创建时间：{created_at}",
        f"- 项目 ID：`{qa['project_id']}`", f"- 交付 SKP：`{delivered['path']}`", f"- SKP SHA-256：`{delivered['sha256']}`",
        f"- 用户确认 ID：`{confirmation['confirmation_id']}`", f"- 用户确认内容：{confirmation['instruction']}", "",
        "## 独立 QA", "", f"- 对齐容差：{qa['tolerance_mm']} mm",
        f"- 模型驱动视图：{len(qa['required_views'])}", "- 全部视图漏项：0", "- 全部视图无依据多项：0", "- 未解决问题：无", "",
        "| Source view | QA view | Role | Max delta (mm) | Overlay |", "|---|---|---|---:|---|"
    ]
    for row in qa["view_results"]:
        lines.append(f"| {row['source_view_id']} | {row['qa_view']} | {row['role']} | {row['metrics']['max_alignment_delta_mm']:.3f} | {row['overlay']['path']} |")
    lines += ["", "## 交付声明", "", "只有工作流 `delivery` 门槛对本清单返回 `PASS` 后，本交付包才可标记为“已交付”。", ""]
    return "\n".join(lines)


def package(project: Path, qa_path: Path, qa: dict[str, Any], package_id: str, created_at: str, output_dir: Path, report_path: Path) -> tuple[dict[str, Any], Path]:
    issues = validate_contract(project, "independent-qa", qa)
    if issues: raise ValueError("Independent QA is invalid:\n- " + "\n- ".join(f"{x.code}@{x.location}: {x.message}" for x in issues))
    if (qa.get("user_confirmation") or {}).get("confirmed") is not True: raise ValueError("Final user acceptance is required before packaging")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", package_id): raise ValueError("Package ID contains unsupported characters")
    build_ref = next((item for item in qa["upstream"] if item.get("kind") == "sketchup-build"), None)
    build_path = resolve(project, (build_ref or {}).get("path")); build = load_json(build_path)
    active = build["active_model"]; active_path = resolve(project, active["path"])
    delivered_path = output_dir / f"{package_id}.skp"
    if delivered_path.exists() or report_path.exists(): raise ValueError("Refusing to overwrite an existing delivery artifact")
    output_dir.mkdir(parents=True, exist_ok=True); report_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(active_path, delivered_path)
    try:
        delivered = _artifact(project, delivered_path)
        report_path.write_text(render_report(package_id, qa, delivered, created_at), encoding="utf-8")
        refs = {item["kind"]: item for item in qa["upstream"] if item.get("kind") in {"source-index", "building-topology", "sketchup-build"}}
        source_contracts = [refs[kind] for kind in ("source-index", "building-topology", "sketchup-build")]
        source_contracts.append({"kind": "independent-qa", "path": relative(project, qa_path), "sha256": sha256_file(qa_path)})
        confirmation = dict(qa["user_confirmation"]); confirmation.pop("confirmed")
        manifest = {
            "schema": SCHEMA, "package_id": package_id, "project_id": qa["project_id"], "revision": qa["revision"],
            "status": "packaged_for_delivery_gate", "created_at": created_at, "project_root": str(project.resolve()),
            "source_contracts": source_contracts, "acceptance": confirmation, "active_model": active,
            "delivered_model": delivered, "evidence": _evidence(qa), "report": _artifact(project, report_path), "unresolved": []
        }
        errors = validate_delivery_manifest(project, manifest)
        if errors: raise ValueError("Delivery package is invalid:\n- " + "\n- ".join(errors))
        return manifest, delivered_path
    except Exception:
        if report_path.is_file(): report_path.unlink()
        if delivered_path.is_file(): delivered_path.unlink()
        raise


def update_state(project: Path, state_path: Path, qa_path: Path, manifest_path: Path) -> None:
    if not state_path.is_file(): return
    state = load_json(state_path); state.setdefault("contracts", {})["independent-qa"] = {"path": relative(project, qa_path), "sha256": sha256_file(qa_path)}
    state["delivery_manifest"] = {"path": relative(project, manifest_path), "sha256": sha256_file(manifest_path)}
    state["status"] = "ready_for_delivery_gate"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create immutable CAD-to-SketchUp delivery package.")
    parser.add_argument("--project", required=True, type=Path); parser.add_argument("--qa", required=True, type=Path)
    parser.add_argument("--package-id"); parser.add_argument("--created-at", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--output-dir", default="output/deliveries", type=Path); parser.add_argument("--report", type=Path)
    parser.add_argument("--manifest", default="work/contracts/delivery-manifest.json", type=Path); parser.add_argument("--state", default="work/workflow-state.json", type=Path)
    args = parser.parse_args(); project = args.project.resolve(); qa_path = resolve(project, args.qa)
    stamp = re.sub(r"[^0-9]", "", args.created_at)[:14]; qa = load_json(qa_path); package_id = args.package_id or f"{qa.get('project_id', 'project')}-{stamp}"
    output_dir = ensure_inside(project, args.output_dir, "delivery output directory"); report = ensure_inside(project, args.report or Path(f"reports/delivery/{package_id}.md"), "delivery report"); manifest_path = ensure_inside(project, args.manifest, "delivery manifest")
    try:
        if manifest_path.exists(): raise ValueError(f"Refusing to overwrite delivery manifest: {manifest_path}")
        manifest, _ = package(project, qa_path, qa, package_id, args.created_at, output_dir, report)
        manifest_path.parent.mkdir(parents=True, exist_ok=True); manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        update_state(project, resolve(project, args.state), qa_path, manifest_path)
    except (OSError, ValueError, TypeError, StopIteration) as exc: print(str(exc)); return 1
    print(json.dumps({"status": "packaged", "manifest": relative(project, manifest_path), "sha256": sha256_file(manifest_path)}, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
