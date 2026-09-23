#!/usr/bin/env python3
"""Shared path, hash, bounds, and view helpers for independent QA."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


MODEL_ROLES = {"plan", "roof_plan", "elevation", "section"}
BOUNDS_KEYS = ("xmin", "ymin", "xmax", "ymax")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(project: Path, value: Any) -> Path:
    path = Path(str(value))
    return (path if path.is_absolute() else project / path).resolve()


def relative(project: Path, path: Path) -> str:
    return str(path.resolve().relative_to(project.resolve())).replace("\\", "/")


def ensure_inside(project: Path, value: Any, label: str) -> Path:
    path = resolve(project, value)
    try:
        path.relative_to(project.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the project root") from exc
    return path


def valid_bounds(bounds: Any) -> bool:
    try:
        return all(key in bounds for key in BOUNDS_KEYS) and float(bounds["xmax"]) > float(bounds["xmin"]) and float(bounds["ymax"]) > float(bounds["ymin"])
    except (TypeError, ValueError):
        return False


def artifact(project: Path, kind: str, path: Path) -> dict[str, str]:
    return {"kind": kind, "path": relative(project, path), "sha256": sha256_file(path)}


def assert_hashed_artifact(project: Path, record: dict[str, Any], label: str) -> Path:
    path = resolve(project, record.get("path"))
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    if sha256_file(path).lower() != str(record.get("sha256") or "").lower():
        raise ValueError(f"{label} hash is stale")
    return path
