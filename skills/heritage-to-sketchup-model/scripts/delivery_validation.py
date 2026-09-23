#!/usr/bin/env python3
"""Semantic validation shared by delivery packaging and the final gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from contract_validation import load_json, validate_contract, validate_schema_file
from independent_qa_common import resolve, sha256_file


CONTRACT_KINDS = {"source-index", "building-topology", "sketchup-build", "independent-qa"}


def _artifact_error(project: Path, record: dict[str, Any], label: str) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    path = resolve(project, record.get("path"))
    if not path.is_file():
        return None, [f"{label} is missing: {path}"]
    actual = sha256_file(path)
    if actual.lower() != str(record.get("sha256") or "").lower():
        errors.append(f"{label} hash is stale")
    return path, errors


def expected_evidence(qa: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    rows: set[tuple[str, str, str, str]] = set()
    for kind, key in (("cad_provenance", "cad_provenance"), ("model_provenance", "model_provenance"), ("machine_result", "machine_result"), ("independent_review", "independent_review")):
        item = qa[key]; rows.add((kind, "", str(item["path"]), str(item["sha256"]).lower()))
    for view in qa.get("view_results") or []:
        for kind in ("cad_view", "model_view", "overlay"):
            item = view[kind]
            rows.add((kind, str(view["source_view_id"]), str(item["path"]), str(item["sha256"]).lower()))
    return rows


def validate_delivery_manifest(project: Path, manifest: dict[str, Any]) -> list[str]:
    errors = [f"{x.code}@{x.location}: {x.message}" for x in validate_schema_file("delivery-manifest.schema.json", manifest)]
    try:
        if Path(str(manifest.get("project_root") or "")).resolve() != project.resolve():
            errors.append("Delivery manifest belongs to a different project root")
    except (OSError, ValueError, TypeError):
        errors.append("Delivery manifest project root is invalid")

    contracts = manifest.get("source_contracts") or []
    kinds = [str(item.get("kind") or "") for item in contracts]
    if set(kinds) != CONTRACT_KINDS or len(kinds) != len(set(kinds)):
        errors.append("Delivery manifest must bind source, topology, build, and accepted QA exactly once")
    loaded: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for item in contracts:
        kind = str(item.get("kind") or "")
        path, artifact_errors = _artifact_error(project, item, f"source contract {kind}")
        errors.extend(artifact_errors)
        if path:
            paths[kind] = path
            try: loaded[kind] = load_json(path)
            except (OSError, ValueError, TypeError) as exc: errors.append(f"Cannot read source contract {kind}: {exc}")
    qa = loaded.get("independent-qa") or {}
    if qa:
        errors.extend(f"accepted QA {x.code}@{x.location}: {x.message}" for x in validate_contract(project, "independent-qa", qa))
        if (qa.get("user_confirmation") or {}).get("confirmed") is not True:
            errors.append("Delivery package requires explicit final user acceptance")
    build = loaded.get("sketchup-build") or {}
    if build:
        errors.extend(f"build {x.code}@{x.location}: {x.message}" for x in validate_contract(project, "sketchup-build", build))

    active_path, active_errors = _artifact_error(project, manifest.get("active_model") or {}, "active model")
    delivered_path, delivered_errors = _artifact_error(project, manifest.get("delivered_model") or {}, "delivered model")
    errors.extend(active_errors + delivered_errors)
    if active_path and delivered_path:
        if active_path.resolve() == delivered_path.resolve(): errors.append("Delivered model must be a new copy, not the active model path")
        try: delivered_path.resolve().relative_to((project / "output" / "deliveries").resolve())
        except ValueError: errors.append("Delivered model must stay under output/deliveries")
        if sha256_file(active_path).lower() != sha256_file(delivered_path).lower(): errors.append("Delivered model bytes differ from the accepted active SKP")
    if build and (build.get("active_model") or {}) != (manifest.get("active_model") or {}):
        errors.append("Delivery manifest active model differs from the verified SketchUp build")

    for item in manifest.get("evidence") or []:
        _, artifact_errors = _artifact_error(project, item, f"delivery evidence {item.get('kind')}")
        errors.extend(artifact_errors)
    actual_evidence = {(str(item.get("kind") or ""), str(item.get("source_view_id") or ""), str(item.get("path") or ""), str(item.get("sha256") or "").lower()) for item in manifest.get("evidence") or []}
    if qa and actual_evidence != expected_evidence(qa):
        errors.append("Delivery evidence does not exactly cover the accepted independent QA package")
    _, report_errors = _artifact_error(project, manifest.get("report") or {}, "delivery report")
    errors.extend(report_errors)
    if qa:
        confirmation = dict(qa.get("user_confirmation") or {}); confirmation.pop("confirmed", None)
        if manifest.get("acceptance") != confirmation: errors.append("Delivery manifest acceptance differs from the accepted QA contract")
    if manifest.get("unresolved"): errors.append("Delivery manifest has unresolved items")
    return errors


def validate_delivery_receipt(project: Path, receipt: dict[str, Any]) -> list[str]:
    """Verify that a DELIVERED receipt still resolves to one valid immutable package."""
    errors = [f"{x.code}@{x.location}: {x.message}" for x in validate_schema_file("delivery-receipt.schema.json", receipt)]
    manifest_path, artifact_errors = _artifact_error(project, receipt.get("delivery_manifest") or {}, "receipt delivery manifest")
    errors.extend(artifact_errors)
    if not manifest_path:
        return errors
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError, TypeError) as exc:
        return errors + [f"Cannot read receipt delivery manifest: {exc}"]
    errors.extend(validate_delivery_manifest(project, manifest))
    qa_ref = next((item for item in manifest.get("source_contracts") or [] if item.get("kind") == "independent-qa"), None)
    if receipt.get("package_id") != manifest.get("package_id"): errors.append("Receipt package differs from delivery manifest")
    if receipt.get("project_id") != manifest.get("project_id"): errors.append("Receipt project differs from delivery manifest")
    if qa_ref and receipt.get("accepted_qa") != {"path": qa_ref.get("path"), "sha256": qa_ref.get("sha256")}: errors.append("Receipt accepted QA differs from delivery manifest")
    if receipt.get("delivered_model") != manifest.get("delivered_model"): errors.append("Receipt delivered model differs from delivery manifest")
    if receipt.get("report") != manifest.get("report"): errors.append("Receipt report differs from delivery manifest")
    if receipt.get("confirmation_id") != (manifest.get("acceptance") or {}).get("confirmation_id"): errors.append("Receipt confirmation differs from delivery manifest")
    gate = receipt.get("gate") or {}
    if gate.get("manifest_sha256") != sha256_file(manifest_path): errors.append("Receipt gate manifest hash is stale")
    if qa_ref and gate.get("qa_sha256") != qa_ref.get("sha256"): errors.append("Receipt gate QA hash differs from delivery manifest")
    if receipt.get("unresolved"): errors.append("Delivery receipt has unresolved items")
    return errors
